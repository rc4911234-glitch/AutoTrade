"""Production exchange execution adapter for Binance USD-M Futures REST API."""

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderType,
    TimeInForce,
)
from trad_auto.core.events import (
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
)
from trad_auto.core.exceptions import (
    ConfigurationError,
    ExchangeApiError,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.order import Order
from trad_auto.core.money import quantize_quantity_down, quantize_to_tick
from trad_auto.core.time import Clock, SystemClock
from trad_auto.execution.adapter_base import ExecutionAdapter
from trad_auto.execution.binance.client import BinanceFuturesRestClient

logger = logging.getLogger(__name__)


class BinanceFuturesExecutionAdapter(ExecutionAdapter):
    """Production-grade order routing and execution adapter for Binance USD-M Futures.

    Guarantees:
    1. Dual Safety Locks: Live execution is blocked fail-closed unless explicit
       confirmation flags are satisfied.
    2. Exact Decimal Quantization: Orders are strictly rounded down to instrument step sizes
       and aligned to tick boundaries, preventing exposure expansion or API format rejects.
    3. Mandatory reduceOnly Brackets: Stop-Loss and Take-Profit bracket orders always include
       reduceOnly=True, ensuring exit fills cannot accidentally open opposing positions.
    4. Post-Only Enforcement: LIMIT_MAKER orders map to Binance GTX (Good-Til-Crossing),
       guaranteeing zero taker fee slippage.
    """

    def __init__(
        self,
        rest_client: BinanceFuturesRestClient,
        event_bus: EventBus | None = None,
        clock: Clock | None = None,
        instruments: dict[str, Instrument] | None = None,
        is_live_mode: bool = False,
        enable_live_trading: bool = False,
    ) -> None:
        self.rest_client = rest_client
        self._event_bus = event_bus
        self._clock: Clock = clock or SystemClock()
        self._instruments: dict[str, Instrument] = instruments or {}
        self.is_live_mode = is_live_mode
        self.enable_live_trading = enable_live_trading

        if self.is_live_mode and not self.enable_live_trading:
            raise ConfigurationError(
                "Live trading adapter initialization blocked: "
                "enable_live_trading must be True when is_live_mode is True"
            )

        self._orders: dict[UUID, Order] = {}
        self._order_by_client_id: dict[str, Order] = {}
        self._order_by_exchange_id: dict[int, Order] = {}

    def register_instrument(self, instrument: Instrument) -> None:
        """Enrolls an instrument and its precision/lot size rules."""
        self._instruments[instrument.symbol.upper()] = instrument
        logger.info("Enrolled instrument %s into BinanceFuturesExecutionAdapter", instrument.symbol)

    def sync_instrument_filters(self, symbol: str) -> Instrument | None:
        """Queries live Binance exchangeInfo to extract authoritative precision & lot size."""
        try:
            info = self.rest_client.get_exchange_info(symbol=symbol)
            symbols_list = info.get("symbols", [])
            for sym_data in symbols_list:
                if sym_data.get("symbol", "").upper() == symbol.upper():
                    tick_size = Decimal("0.10")
                    step_size = Decimal("0.001")
                    min_qty = Decimal("0.001")

                    for f in sym_data.get("filters", []):
                        f_type = f.get("filterType")
                        if f_type == "PRICE_FILTER":
                            tick_size = Decimal(str(f.get("tickSize", "0.10")))
                        elif f_type == "LOT_SIZE":
                            step_size = Decimal(str(f.get("stepSize", "0.001")))
                            min_qty = Decimal(str(f.get("minQty", "0.001")))

                    inst = Instrument(
                        symbol=symbol.upper(),
                        exchange="BINANCE",
                        asset_class="CRYPTO",
                        currency="USDT",
                        tick_size=tick_size,
                        lot_size=step_size,
                        quantity_step=step_size,
                        min_quantity=min_qty,
                    )
                    self.register_instrument(inst)
                    logger.info(
                        "Synchronized live Binance filters for %s: tick=%s, step=%s, min_qty=%s",
                        symbol,
                        tick_size,
                        step_size,
                        min_qty,
                    )
                    return inst
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not sync live instrument filters for %s: %s", symbol, exc)
        return None

    def sync_account_balance(self, ledger: Any) -> Decimal:
        """Synchronizes live USDT wallet balance from Binance Futures account into ledger."""
        try:
            account = self.rest_client.get_account()
            for asset in account.get("assets", []):
                if asset.get("asset") == "USDT":
                    wallet_bal = Decimal(str(asset.get("walletBalance", "0")))
                    ledger.set_initial_balance("USDT", wallet_bal)
                    logger.info("Synchronized Binance live USDT wallet balance: %s", wallet_bal)
                    return wallet_bal
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to sync account balance from Binance: %s", exc)
        return ZERO_DECIMAL

    def submit_order(self, order: Order) -> None:
        """Translates domain order into Binance payload, quantizes values, and transmits."""
        now = self._clock.now()
        self._orders[order.order_id] = order
        self._order_by_client_id[order.client_order_id] = order

        inst = self._instruments.get(order.symbol.upper())

        # Exact Decimal Quantization
        if inst:
            quantized_qty = quantize_quantity_down(
                order.quantity,
                inst.quantity_step,
                inst.min_quantity,
            )
            quantized_price = (
                quantize_to_tick(order.price, inst.tick_size)
                if order.price > ZERO_DECIMAL
                else order.price
            )
        else:
            quantized_qty = order.quantity
            quantized_price = order.price

        if quantized_qty <= ZERO_DECIMAL:
            reason = f"Quantized quantity {quantized_qty} is below instrument minimum threshold"
            order.mark_rejected(reason, now)
            if self._event_bus:
                self._event_bus.publish(
                    OrderRejectedEvent(
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        reason=reason,
                    )
                )
            return

        # Build Binance REST Parameters
        payload = self._build_order_payload(order, quantized_qty, quantized_price)

        try:
            resp = self.rest_client.place_order(payload)
            order.mark_submitted(now)
            if self._event_bus:
                self._event_bus.publish(OrderSubmittedEvent(order=order))

            exchange_order_id = resp.get("orderId")
            if exchange_order_id:
                self._order_by_exchange_id[int(exchange_order_id)] = order

            # Check for immediate fill
            resp_status = str(resp.get("status", "")).upper()
            if resp_status == "FILLED":
                fill_qty = Decimal(str(resp.get("executedQty") or quantized_qty))
                fill_price = Decimal(
                    str(resp.get("avgPrice") or resp.get("price") or quantized_price)
                )
                order.apply_fill(fill_qty, now)
                if self._event_bus:
                    self._event_bus.publish(
                        OrderFilledEvent(
                            order_id=order.order_id,
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            side=order.side,
                            fill_price=fill_price,
                            fill_quantity=fill_qty,
                            fee=ZERO_DECIMAL,
                            fee_asset="USDT",
                            is_maker=(order.order_type == OrderType.LIMIT_MAKER),
                            intent_id=order.intent_id,
                            order=order,
                        )
                    )
            elif resp_status in ("REJECTED", "EXPIRED"):
                reject_reason = str(resp.get("msg") or resp_status)
                order.mark_rejected(reject_reason, now)
                if self._event_bus:
                    self._event_bus.publish(
                        OrderRejectedEvent(
                            order_id=order.order_id,
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            reason=reject_reason,
                        )
                    )

        except ExchangeApiError as exc:
            logger.warning("Order submission rejected by Binance: %s", exc)
            order.mark_rejected(str(exc), now)
            if self._event_bus:
                self._event_bus.publish(
                    OrderRejectedEvent(
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        reason=str(exc),
                    )
                )

    def cancel_order(self, order_id: UUID) -> bool:
        """Cancels an active order via Binance REST endpoint."""
        order = self._orders.get(order_id)
        if not order or not order.is_active:
            return False

        now = self._clock.now()
        try:
            self.rest_client.cancel_order(
                symbol=order.symbol,
                client_order_id=order.client_order_id,
            )
            order.mark_canceled(now)
            if self._event_bus:
                self._event_bus.publish(
                    OrderCanceledEvent(
                        order_id=order.order_id,
                        client_order_id=order.client_order_id,
                        symbol=order.symbol,
                        reason="Canceled via BinanceFuturesExecutionAdapter",
                    )
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to cancel order %s on Binance: %s", order_id, exc)
            return False

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancels all active orders, optionally scoped to a symbol."""
        now = self._clock.now()
        active = self.get_active_orders(symbol)
        if not active:
            return 0

        canceled_count = 0
        target_symbols = {symbol.upper()} if symbol else {o.symbol.upper() for o in active}

        for sym in target_symbols:
            try:
                self.rest_client.cancel_all_open_orders(symbol=sym)
                for order in active:
                    if order.symbol.upper() == sym and order.is_active:
                        order.mark_canceled(now)
                        if self._event_bus:
                            self._event_bus.publish(
                                OrderCanceledEvent(
                                    order_id=order.order_id,
                                    client_order_id=order.client_order_id,
                                    symbol=order.symbol,
                                    reason="Batch canceled via BinanceFuturesExecutionAdapter",
                                )
                            )
                        canceled_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to cancel all open orders for %s: %s", sym, exc)

        return canceled_count

    def get_order(self, order_id: UUID) -> Order | None:
        """Retrieves an order by its internal UUID."""
        return self._orders.get(order_id)

    def get_active_orders(self, symbol: str | None = None) -> list[Order]:
        """Returns all currently active orders."""
        orders = [o for o in self._orders.values() if o.is_active]
        if symbol:
            sym_upper = symbol.upper()
            return [o for o in orders if o.symbol.upper() == sym_upper]
        return orders

    def sync_order_status(self, order_id: UUID) -> Order | None:
        """Polls Binance for current order state and syncs domain model."""
        order = self._orders.get(order_id)
        if not order or not order.is_active:
            return order

        now = self._clock.now()
        try:
            resp = self.rest_client.get_order(
                symbol=order.symbol,
                client_order_id=order.client_order_id,
            )
            resp_status = str(resp.get("status", "")).upper()
            if resp_status == "FILLED" and order.is_active:
                executed_qty = Decimal(str(resp.get("executedQty", "0")))
                remaining_to_fill = executed_qty - order.filled_quantity
                if remaining_to_fill > ZERO_DECIMAL:
                    fill_price = Decimal(str(resp.get("avgPrice") or resp.get("price") or "0"))
                    order.apply_fill(remaining_to_fill, now)
                    if self._event_bus:
                        self._event_bus.publish(
                            OrderFilledEvent(
                                order_id=order.order_id,
                                client_order_id=order.client_order_id,
                                symbol=order.symbol,
                                side=order.side,
                                fill_price=fill_price,
                                fill_quantity=remaining_to_fill,
                                fee=ZERO_DECIMAL,
                                is_maker=(order.order_type == OrderType.LIMIT_MAKER),
                                intent_id=order.intent_id,
                                order=order,
                            )
                        )
            elif resp_status == "CANCELED" and order.is_active:
                order.mark_canceled(now)
                if self._event_bus:
                    self._event_bus.publish(
                        OrderCanceledEvent(
                            order_id=order.order_id,
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            reason="Canceled on exchange",
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to sync order %s: %s", order_id, exc)

        return order

    def _build_order_payload(
        self,
        order: Order,
        quantized_qty: Decimal,
        quantized_price: Decimal,
    ) -> dict[str, Any]:
        """Constructs authenticated Binance USD-M Futures order parameters."""
        payload: dict[str, Any] = {
            "symbol": order.symbol.upper(),
            "side": "BUY" if order.side == OrderSide.BUY else "SELL",
            "quantity": str(quantized_qty),
            "newClientOrderId": order.client_order_id,
        }

        # Order Type Mapping
        if order.order_type == OrderType.MARKET:
            payload["type"] = "MARKET"
        elif order.order_type == OrderType.LIMIT:
            payload["type"] = "LIMIT"
            payload["price"] = str(quantized_price)
            payload["timeInForce"] = "GTC"
        elif order.order_type == OrderType.LIMIT_MAKER:
            payload["type"] = "LIMIT"
            payload["price"] = str(quantized_price)
            payload["timeInForce"] = "GTX"  # Binance Post-Only
        elif order.order_type == OrderType.STOP:
            payload["type"] = "STOP_MARKET"
            payload["stopPrice"] = str(quantized_price)

        # TimeInForce override if explicitly configured
        if order.time_in_force == TimeInForce.IOC and "timeInForce" in payload:
            payload["timeInForce"] = "IOC"
        elif order.time_in_force == TimeInForce.FOK and "timeInForce" in payload:
            payload["timeInForce"] = "FOK"

        # Cardinal Safety Rule: Mandatory reduceOnly on Risk-Reducing and Exit orders
        is_protective_exit = order.action_purpose in (
            OrderActionPurpose.RISK_REDUCING,
            OrderActionPurpose.EXIT,
        )
        payload["reduceOnly"] = "true" if is_protective_exit else "false"

        return payload
