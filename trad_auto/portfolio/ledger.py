"""Position ledger and multi-currency portfolio manager."""

from datetime import UTC, date, datetime
from decimal import Decimal

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide, PositionSide, PositionStatus
from trad_auto.core.events import (
    BalanceUpdatedEvent,
    FundingPaymentEvent,
    FundingRateEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    PositionUpdatedEvent,
    QuoteUpdatedEvent,
)
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.portfolio import Balance, Position, PositionLot
from trad_auto.core.time import Clock, SystemClock
from trad_auto.portfolio.fifo_tracker import FifoPositionTracker


class PositionLedger:
    """Institutional multi-currency ledger and position tracker.

    Maintains cash and margin balances, tracks open lots via FIFO,
    continuously marks open positions to market upon live quote updates,
    and applies perpetual swap funding rate debits/credits.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        clock: Clock | None = None,
        default_quote_asset: str = "USDT",
    ) -> None:
        self._event_bus = event_bus
        self._clock: Clock = clock or SystemClock()
        self._default_quote_asset = default_quote_asset

        self._balances: dict[str, Balance] = {}
        self._trackers: dict[str, FifoPositionTracker] = {}
        self._positions: dict[str, Position] = {}
        self._realized_trades: list[tuple[datetime, Decimal]] = []

        if self._event_bus is not None:
            self._event_bus.subscribe(QuoteUpdatedEvent, self._handle_quote_updated)
            self._event_bus.subscribe(FundingRateEvent, self._handle_funding_rate)

    def set_initial_balance(self, asset: str, amount: Decimal) -> None:
        """Initializes or overwrites the balance for an asset."""
        if amount < ZERO_DECIMAL:
            raise DomainValidationError("Initial balance cannot be negative")
        self._balances[asset] = Balance(asset=asset, free=amount, locked=ZERO_DECIMAL)
        if self._event_bus:
            self._event_bus.publish(
                BalanceUpdatedEvent(
                    asset=asset,
                    free=amount,
                    locked=ZERO_DECIMAL,
                    total=amount,
                    reason="INITIAL_BALANCE",
                )
            )

    def get_balance(self, asset: str) -> Balance:
        """Retrieves or creates a zero balance for an asset."""
        if asset not in self._balances:
            self._balances[asset] = Balance(asset=asset, free=ZERO_DECIMAL, locked=ZERO_DECIMAL)
        return self._balances[asset]

    def deposit(self, asset: str, amount: Decimal) -> None:
        """Deposits funds into the asset's free balance."""
        balance = self.get_balance(asset)
        balance.credit_free(amount)
        if self._event_bus:
            self._event_bus.publish(
                BalanceUpdatedEvent(
                    asset=asset,
                    free=balance.free,
                    locked=balance.locked,
                    total=balance.total,
                    reason="DEPOSIT",
                )
            )

    def withdraw(self, asset: str, amount: Decimal) -> None:
        """Withdraws funds from the asset's free balance."""
        balance = self.get_balance(asset)
        balance.debit_free(amount)
        if self._event_bus:
            self._event_bus.publish(
                BalanceUpdatedEvent(
                    asset=asset,
                    free=balance.free,
                    locked=balance.locked,
                    total=balance.total,
                    reason="WITHDRAWAL",
                )
            )

    def get_position(self, symbol: str) -> Position | None:
        """Returns the current position for a symbol if open, else None."""
        pos = self._positions.get(symbol)
        if pos is not None and pos.is_open:
            return pos
        return None

    def get_open_positions(self) -> list[Position]:
        """Returns a list of all currently active open positions."""
        return [p for p in self._positions.values() if p.is_open]

    def record_fill(
        self,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal,
        fee: Decimal,
        timestamp: datetime,
        fee_asset: str = "USDT",
        quote_asset: str = "USDT",
    ) -> None:
        """Processes an executed order fill.

        Handles opening new positions, scaling into existing positions,
        and closing/reducing positions using FIFO lot consumption.
        """
        if quantity <= ZERO_DECIMAL:
            raise DomainValidationError("Fill quantity must be strictly positive")
        if price <= ZERO_DECIMAL:
            raise DomainValidationError("Fill price must be strictly positive")
        if fee < ZERO_DECIMAL:
            raise DomainValidationError("Fill fee cannot be negative")
        if timestamp.tzinfo is None or timestamp.tzinfo != UTC:
            raise DomainValidationError("Fill timestamp must be timezone-aware (UTC)")

        # Deduct trading fee from fee asset balance if positive
        if fee > ZERO_DECIMAL:
            fee_bal = self.get_balance(fee_asset)
            fee_bal.debit_free(fee)
            if self._event_bus:
                self._event_bus.publish(
                    BalanceUpdatedEvent(
                        asset=fee_asset,
                        free=fee_bal.free,
                        locked=fee_bal.locked,
                        total=fee_bal.total,
                        reason="TRADING_FEE",
                    )
                )

        active_pos = self.get_position(symbol)

        if active_pos is None:
            # 1. Open new position
            target_side = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT
            tracker = FifoPositionTracker(symbol=symbol, side=target_side)
            new_lot = PositionLot(
                symbol=symbol,
                side=target_side,
                entry_price=price,
                initial_quantity=quantity,
                remaining_quantity=quantity,
                timestamp=timestamp,
                fee_paid=fee,
            )
            tracker.add_lot(new_lot)
            self._trackers[symbol] = tracker

            pos = Position(
                symbol=symbol,
                side=target_side,
                quantity=quantity,
                average_entry_price=price,
                mark_price=price,
                lots=[new_lot],
                unrealized_pnl=ZERO_DECIMAL,
                realized_pnl=ZERO_DECIMAL,
                status=PositionStatus.OPEN,
                updated_at=timestamp,
            )
            self._positions[symbol] = pos

            if self._event_bus:
                self._event_bus.publish(
                    PositionOpenedEvent(
                        symbol=symbol,
                        side=target_side,
                        quantity=quantity,
                        entry_price=price,
                        lot_id=new_lot.lot_id,
                    )
                )
            return

        # An existing position exists
        is_increasing = (active_pos.side == PositionSide.LONG and side == OrderSide.BUY) or (
            active_pos.side == PositionSide.SHORT and side == OrderSide.SELL
        )

        if is_increasing:
            # 2. Scale into existing position (add lot)
            tracker = self._trackers[symbol]
            new_lot = PositionLot(
                symbol=symbol,
                side=active_pos.side,
                entry_price=price,
                initial_quantity=quantity,
                remaining_quantity=quantity,
                timestamp=timestamp,
                fee_paid=fee,
            )
            tracker.add_lot(new_lot)

            active_pos.quantity = tracker.total_quantity
            active_pos.average_entry_price = tracker.average_entry_price
            active_pos.lots = list(tracker.open_lots)
            active_pos.updated_at = timestamp
            active_pos.update_mark_price(price, timestamp)

            if self._event_bus:
                self._event_bus.publish(
                    PositionUpdatedEvent(
                        symbol=symbol,
                        side=active_pos.side,
                        quantity=active_pos.quantity,
                        average_entry_price=active_pos.average_entry_price,
                        mark_price=active_pos.mark_price,
                        unrealized_pnl=active_pos.unrealized_pnl,
                    )
                )
        else:
            # 3. Reduce or close existing position (opposite order)
            tracker = self._trackers[symbol]
            close_qty = min(quantity, active_pos.quantity)
            fill_result = tracker.close_lots(quantity=close_qty, exit_price=price, exit_fee=fee)

            # Record realized PnL
            self._realized_trades.append((timestamp, fill_result.realized_pnl))
            active_pos.realized_pnl += fill_result.realized_pnl

            # Settle PnL in quote asset balance
            quote_bal = self.get_balance(quote_asset)
            if fill_result.realized_pnl > ZERO_DECIMAL:
                quote_bal.credit_free(fill_result.realized_pnl)
            elif fill_result.realized_pnl < ZERO_DECIMAL:
                quote_bal.debit_free(abs(fill_result.realized_pnl))

            if tracker.is_empty:
                # Position is completely closed
                active_pos.quantity = ZERO_DECIMAL
                active_pos.status = PositionStatus.CLOSED
                active_pos.unrealized_pnl = ZERO_DECIMAL
                active_pos.lots = []
                active_pos.updated_at = timestamp

                if self._event_bus:
                    self._event_bus.publish(
                        PositionClosedEvent(
                            symbol=symbol,
                            side=active_pos.side,
                            closed_quantity=close_qty,
                            exit_price=price,
                            realized_pnl=fill_result.realized_pnl,
                            total_fees=fee,
                        )
                    )
            else:
                # Partially closed
                active_pos.quantity = tracker.total_quantity
                active_pos.average_entry_price = tracker.average_entry_price
                active_pos.lots = list(tracker.open_lots)
                active_pos.updated_at = timestamp
                active_pos.update_mark_price(price, timestamp)

                if self._event_bus:
                    self._event_bus.publish(
                        PositionUpdatedEvent(
                            symbol=symbol,
                            side=active_pos.side,
                            quantity=active_pos.quantity,
                            average_entry_price=active_pos.average_entry_price,
                            mark_price=active_pos.mark_price,
                            unrealized_pnl=active_pos.unrealized_pnl,
                        )
                    )

            if self._event_bus:
                self._event_bus.publish(
                    BalanceUpdatedEvent(
                        asset=quote_asset,
                        free=quote_bal.free,
                        locked=quote_bal.locked,
                        total=quote_bal.total,
                        reason="REALIZED_PNL_SETTLEMENT",
                    )
                )

            # Handle reversal if remaining quantity > 0
            excess_qty = quantity - close_qty
            if excess_qty > ZERO_DECIMAL:
                new_side = PositionSide.LONG if side == OrderSide.BUY else PositionSide.SHORT
                new_tracker = FifoPositionTracker(symbol=symbol, side=new_side)
                reverse_lot = PositionLot(
                    symbol=symbol,
                    side=new_side,
                    entry_price=price,
                    initial_quantity=excess_qty,
                    remaining_quantity=excess_qty,
                    timestamp=timestamp,
                    fee_paid=ZERO_DECIMAL,
                )
                new_tracker.add_lot(reverse_lot)
                self._trackers[symbol] = new_tracker

                new_pos = Position(
                    symbol=symbol,
                    side=new_side,
                    quantity=excess_qty,
                    average_entry_price=price,
                    mark_price=price,
                    lots=[reverse_lot],
                    unrealized_pnl=ZERO_DECIMAL,
                    realized_pnl=ZERO_DECIMAL,
                    status=PositionStatus.OPEN,
                    updated_at=timestamp,
                )
                self._positions[symbol] = new_pos

                if self._event_bus:
                    self._event_bus.publish(
                        PositionOpenedEvent(
                            symbol=symbol,
                            side=new_side,
                            quantity=excess_qty,
                            entry_price=price,
                            lot_id=reverse_lot.lot_id,
                        )
                    )

    def _handle_quote_updated(self, event: QuoteUpdatedEvent) -> None:
        """Mark-to-Market: continuously updates open positions on live quote updates."""
        quote = event.quote
        if quote is None:
            return

        active_pos = self.get_position(quote.symbol)
        if active_pos is not None:
            active_pos.update_mark_price(quote.mid_price, quote.timestamp)
            if self._event_bus:
                self._event_bus.publish(
                    PositionUpdatedEvent(
                        symbol=quote.symbol,
                        side=active_pos.side,
                        quantity=active_pos.quantity,
                        average_entry_price=active_pos.average_entry_price,
                        mark_price=active_pos.mark_price,
                        unrealized_pnl=active_pos.unrealized_pnl,
                    )
                )

    def _handle_funding_rate(self, event: FundingRateEvent) -> None:
        """Processes perpetual futures funding rate settlement."""
        funding = event.funding_rate
        if funding is None:
            return

        active_pos = self.get_position(funding.symbol)
        if active_pos is None or not active_pos.is_open:
            return

        # Funding Payment = Position Size * Mark Price * Funding Rate
        # Positive funding rate: Longs pay Shorts.
        # Negative funding rate: Shorts pay Longs.
        notional = active_pos.quantity * active_pos.mark_price
        raw_payment = notional * funding.rate

        quote_bal = self.get_balance(self._default_quote_asset)
        if active_pos.side == PositionSide.LONG:
            # Long pays if raw_payment > 0, receives if raw_payment < 0
            if raw_payment > ZERO_DECIMAL:
                quote_bal.debit_free(raw_payment)
            elif raw_payment < ZERO_DECIMAL:
                quote_bal.credit_free(abs(raw_payment))
            net_payment = -raw_payment
        else:
            # Short receives if raw_payment > 0, pays if raw_payment < 0
            if raw_payment > ZERO_DECIMAL:
                quote_bal.credit_free(raw_payment)
            elif raw_payment < ZERO_DECIMAL:
                quote_bal.debit_free(abs(raw_payment))
            net_payment = raw_payment

        if self._event_bus:
            self._event_bus.publish(
                FundingPaymentEvent(
                    symbol=funding.symbol,
                    payment_amount=net_payment,
                    funding_rate=funding.rate,
                )
            )
            self._event_bus.publish(
                BalanceUpdatedEvent(
                    asset=self._default_quote_asset,
                    free=quote_bal.free,
                    locked=quote_bal.locked,
                    total=quote_bal.total,
                    reason="FUNDING_PAYMENT",
                )
            )

    # Risk Bridge Metric Queries
    def get_total_deployed_capital(self) -> Decimal:
        """Calculates total cost basis deployed across all open positions."""
        return sum(
            (p.quantity * p.average_entry_price for p in self.get_open_positions()),
            ZERO_DECIMAL,
        )

    def get_current_exposure(self, symbol: str | None = None) -> Decimal:
        """Returns gross mark-to-market exposure (notional value)."""
        if symbol:
            pos = self.get_position(symbol)
            return pos.notional_value if pos else ZERO_DECIMAL
        return sum((p.notional_value for p in self.get_open_positions()), ZERO_DECIMAL)

    def get_today_realized_pnl(self, current_date: date | None = None) -> Decimal:
        """Calculates cumulative realized P&L settled on current date."""
        target_date = current_date or self._clock.now().date()
        return sum(
            (pnl for ts, pnl in self._realized_trades if ts.date() == target_date),
            ZERO_DECIMAL,
        )

    def get_current_unrealized_pnl(self) -> Decimal:
        """Returns total mark-to-market unrealized P&L across all open positions."""
        return sum((p.unrealized_pnl for p in self.get_open_positions()), ZERO_DECIMAL)

    def get_open_positions_count(self) -> int:
        """Returns the number of active open positions."""
        return len(self.get_open_positions())
