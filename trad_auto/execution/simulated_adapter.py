"""Simulated execution adapter with realistic fill simulation and fee tiers."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import OrderSide, OrderType
from trad_auto.core.events import (
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.market_data import Quote, Trade
from trad_auto.core.models.order import Order
from trad_auto.core.time import Clock, SystemClock
from trad_auto.execution.adapter_base import ExecutionAdapter


class SimulatedExecutionAdapter(ExecutionAdapter):
    """Simulated execution venue for paper trading and backtesting.

    Models realistic fills against top-of-book bid/ask quotes, enforces
    strict post-only (LIMIT_MAKER) spread rules, applies maker/taker fee tiers,
    and models market slippage.
    """

    def __init__(
        self,
        event_bus: EventBus,
        clock: Clock | None = None,
        maker_fee_rate: Decimal = Decimal("0.0002"),  # 0.02%
        taker_fee_rate: Decimal = Decimal("0.0005"),  # 0.05%
        slippage_rate: Decimal = Decimal("0.0001"),  # 0.01%
    ) -> None:
        self._event_bus = event_bus
        self._clock: Clock = clock or SystemClock()
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.slippage_rate = slippage_rate

        self._orders: dict[UUID, Order] = {}
        self._active_orders: dict[UUID, Order] = {}
        self._latest_quotes: dict[str, Quote] = {}
        self._instruments: dict[str, Instrument] = {}

        self._event_bus.subscribe(QuoteUpdatedEvent, self._handle_quote_updated)
        self._event_bus.subscribe(TradeReceivedEvent, self._handle_trade_received)

    def register_instrument(self, instrument: Instrument) -> None:
        """Enrolls an instrument into simulated adapter."""
        self._instruments[instrument.symbol.upper()] = instrument

    def submit_order(self, order: Order) -> None:
        """Processes and routes an incoming order."""
        now = self._clock.now()
        self._orders[order.order_id] = order
        quote = self._latest_quotes.get(order.symbol)

        if order.order_type == OrderType.MARKET:
            self._execute_market_order(order, quote, now)
            return

        if order.order_type == OrderType.LIMIT_MAKER:
            # Post-only check: cannot cross existing top-of-book spread
            if quote is not None:
                crosses_spread = (
                    order.side == OrderSide.BUY and order.price >= quote.ask_price
                ) or (order.side == OrderSide.SELL and order.price <= quote.bid_price)
                if crosses_spread:
                    order.mark_rejected("Post-only order would cross spread as taker", now)
                    self._event_bus.publish(
                        OrderRejectedEvent(
                            order_id=order.order_id,
                            client_order_id=order.client_order_id,
                            symbol=order.symbol,
                            reason="Post-only order would cross spread as taker",
                        )
                    )
                    return

        # Limit / Stop / valid LimitMaker order enters book as SUBMITTED
        order.mark_submitted(now)
        self._active_orders[order.order_id] = order
        self._event_bus.publish(OrderSubmittedEvent(order=order))

        # Check if immediate fill occurs against current quote (for standard LIMIT)
        if order.order_type == OrderType.LIMIT and quote is not None:
            self._match_order_against_quote(order, quote, now)

    def cancel_order(self, order_id: UUID) -> bool:
        """Cancels an active order."""
        order = self._active_orders.get(order_id)
        if order is None:
            return False

        now = self._clock.now()
        order.mark_canceled(now)
        del self._active_orders[order_id]
        self._event_bus.publish(
            OrderCanceledEvent(
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                reason="User / Engine requested cancellation",
            )
        )
        return True

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancels all active orders, optionally scoped to a symbol."""
        targets = [
            order
            for order in self._active_orders.values()
            if symbol is None or order.symbol == symbol
        ]
        count = 0
        for order in targets:
            if self.cancel_order(order.order_id):
                count += 1
        return count

    def get_order(self, order_id: UUID) -> Order | None:
        return self._orders.get(order_id)

    def get_active_orders(self, symbol: str | None = None) -> list[Order]:
        return [
            order
            for order in self._active_orders.values()
            if symbol is None or order.symbol == symbol
        ]

    def _execute_market_order(
        self,
        order: Order,
        quote: Quote | None,
        timestamp: datetime,
    ) -> None:
        """Fills a market order immediately against top-of-book with slippage."""
        if quote is None:
            order.mark_rejected("No market quote available for market execution", timestamp)
            self._event_bus.publish(
                OrderRejectedEvent(
                    order_id=order.order_id,
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    reason="No market quote available for market execution",
                )
            )
            return

        order.mark_submitted(timestamp)

        if order.side == OrderSide.BUY:
            fill_price = quote.ask_price * (Decimal("1.0") + self.slippage_rate)
        else:
            fill_price = quote.bid_price * (Decimal("1.0") - self.slippage_rate)

        fill_qty = order.remaining_quantity
        fee = fill_price * fill_qty * self.taker_fee_rate

        order.apply_fill(fill_qty, timestamp)

        self._event_bus.publish(
            OrderFilledEvent(
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                fill_price=fill_price,
                fill_quantity=fill_qty,
                fee=fee,
                fee_asset="USDT",
                is_maker=False,
                intent_id=order.intent_id,
                order=order,
                timestamp=timestamp,
            )
        )

    def _handle_quote_updated(self, event: QuoteUpdatedEvent) -> None:
        """Matches resting orders against new top-of-book quotes."""
        quote = event.quote
        if quote is None:
            return

        self._latest_quotes[quote.symbol] = quote
        now = self._clock.now()

        active_symbol_orders = [o for o in self._active_orders.values() if o.symbol == quote.symbol]
        for order in active_symbol_orders:
            self._match_order_against_quote(order, quote, now)

    def _handle_trade_received(self, event: TradeReceivedEvent) -> None:
        """Matches resting orders against executed trades (ticks)."""
        trade = event.trade
        if trade is None:
            return

        now = self._clock.now()
        active_symbol_orders = [o for o in self._active_orders.values() if o.symbol == trade.symbol]
        for order in active_symbol_orders:
            self._match_order_against_trade(order, trade, now)

    def _match_order_against_quote(
        self,
        order: Order,
        quote: Quote,
        timestamp: datetime,
    ) -> None:
        """Evaluates whether an active order fills against the quote."""
        if order.order_id not in self._active_orders:
            return

        if order.order_type in (OrderType.LIMIT, OrderType.LIMIT_MAKER):
            if order.side == OrderSide.BUY and quote.ask_price <= order.price:
                self._fill_resting_order(order, order.price, is_maker=True, timestamp=timestamp)
            elif order.side == OrderSide.SELL and quote.bid_price >= order.price:
                self._fill_resting_order(order, order.price, is_maker=True, timestamp=timestamp)

        elif order.order_type == OrderType.STOP:
            if order.side == OrderSide.BUY and quote.ask_price >= order.price:
                fill_price = quote.ask_price * (Decimal("1.0") + self.slippage_rate)
                self._fill_resting_order(order, fill_price, is_maker=False, timestamp=timestamp)
            elif order.side == OrderSide.SELL and quote.bid_price <= order.price:
                fill_price = quote.bid_price * (Decimal("1.0") - self.slippage_rate)
                self._fill_resting_order(order, fill_price, is_maker=False, timestamp=timestamp)

    def _match_order_against_trade(
        self,
        order: Order,
        trade: Trade,
        timestamp: datetime,
    ) -> None:
        """Evaluates whether an active order fills against a trade tick."""
        if order.order_id not in self._active_orders:
            return

        if order.order_type in (OrderType.LIMIT, OrderType.LIMIT_MAKER):
            if order.side == OrderSide.BUY and trade.price <= order.price:
                self._fill_resting_order(order, order.price, is_maker=True, timestamp=timestamp)
            elif order.side == OrderSide.SELL and trade.price >= order.price:
                self._fill_resting_order(order, order.price, is_maker=True, timestamp=timestamp)

        elif order.order_type == OrderType.STOP:
            if order.side == OrderSide.BUY and trade.price >= order.price:
                fill_price = trade.price * (Decimal("1.0") + self.slippage_rate)
                self._fill_resting_order(order, fill_price, is_maker=False, timestamp=timestamp)
            elif order.side == OrderSide.SELL and trade.price <= order.price:
                fill_price = trade.price * (Decimal("1.0") - self.slippage_rate)
                self._fill_resting_order(order, fill_price, is_maker=False, timestamp=timestamp)

    def _fill_resting_order(
        self,
        order: Order,
        fill_price: Decimal,
        is_maker: bool,
        timestamp: datetime,
    ) -> None:
        """Completes a fill for a resting order."""
        fill_qty = order.remaining_quantity
        fee_rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
        fee = fill_price * fill_qty * fee_rate

        order.apply_fill(fill_qty, timestamp)
        del self._active_orders[order.order_id]

        self._event_bus.publish(
            OrderFilledEvent(
                order_id=order.order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                fill_price=fill_price,
                fill_quantity=fill_qty,
                fee=fee,
                fee_asset="USDT",
                is_maker=is_maker,
                intent_id=order.intent_id,
                order=order,
                timestamp=timestamp,
            )
        )
