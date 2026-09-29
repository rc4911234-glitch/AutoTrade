"""OrderManager and Execution Engine coordinating order lifecycle and bracket stops."""

from decimal import Decimal
from uuid import UUID

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderType,
    PositionSide,
    SessionState,
    TimeInForce,
)
from trad_auto.core.events import (
    OrderFilledEvent,
    QuoteUpdatedEvent,
    TradeIntentCreatedEvent,
    TradingSessionStateChangedEvent,
)
from trad_auto.core.models.intent import TradeIntent
from trad_auto.core.models.order import Order
from trad_auto.core.time import Clock, SystemClock
from trad_auto.execution.adapter_base import ExecutionAdapter
from trad_auto.portfolio.ledger import PositionLedger


class OrderManager:
    """Institutional execution coordinator.

    1. Translates approved TradeIntent contracts into exchange orders.
    2. Enforces post-only LIMIT_MAKER for entry fee optimization.
    3. Automatically places bracket Stop-Loss and Take-Profit orders upon fill.
    4. Enforces One-Cancels-the-Other (OCO) logic between bracket children.
    5. Directly updates PositionLedger on fills.
    6. Flattens open positions upon emergency stop transitions.
    """

    def __init__(
        self,
        event_bus: EventBus,
        adapter: ExecutionAdapter,
        ledger: PositionLedger,
        clock: Clock | None = None,
        enable_trailing_stop: bool = False,
        breakeven_offset_ratio: Decimal = Decimal("0.0075"),
        breakeven_fee_buffer: Decimal = Decimal("0.0008"),
        trailing_offset_ratio: Decimal = Decimal("0.012"),
        trailing_delta_ratio: Decimal = Decimal("0.005"),
        entry_timeout_seconds: float = 60.0,
        enable_orphan_watchdog: bool = False,
        trailing_min_ratchet_pct: Decimal = Decimal("0.0005"),
    ) -> None:
        self._event_bus = event_bus
        self._adapter = adapter
        self._ledger = ledger
        self._clock: Clock = clock or SystemClock()
        self.enable_trailing_stop = enable_trailing_stop
        self.breakeven_offset_ratio = breakeven_offset_ratio
        self.breakeven_fee_buffer = breakeven_fee_buffer
        self.trailing_offset_ratio = trailing_offset_ratio
        self.trailing_delta_ratio = trailing_delta_ratio
        self.entry_timeout_seconds = entry_timeout_seconds
        self.enable_orphan_watchdog = enable_orphan_watchdog
        self.trailing_min_ratchet_pct = trailing_min_ratchet_pct
        self._slippage_records: list[Decimal] = []

        self._intents_by_order: dict[UUID, TradeIntent] = {}
        self._bracket_children: dict[UUID, list[UUID]] = {}
        self._parent_by_child: dict[UUID, UUID] = {}
        self._active_stop_by_entry: dict[UUID, Order] = {}
        self._entry_by_symbol: dict[str, UUID] = {}
        self._peak_prices: dict[str, Decimal] = {}

        self._event_bus.subscribe(TradeIntentCreatedEvent, self._handle_trade_intent)
        self._event_bus.subscribe(OrderFilledEvent, self._handle_order_filled)
        self._event_bus.subscribe(
            TradingSessionStateChangedEvent, self._handle_session_state_changed
        )
        self._event_bus.subscribe(QuoteUpdatedEvent, self._handle_quote_updated)

    def _handle_trade_intent(self, event: TradeIntentCreatedEvent) -> None:
        """Processes approved TradeIntent and submits the initial entry order."""
        intent = event.intent
        if intent is None:
            return

        now = self._clock.now()
        # Create entry order
        order = Order(
            symbol=intent.symbol,
            side=intent.direction,
            order_type=intent.order_type,
            price=intent.entry_price,
            quantity=intent.quantity,
            client_order_id=f"ENTRY-{intent.intent_id.hex[:8]}",
            action_purpose=OrderActionPurpose.NEW_ENTRY,
            time_in_force=TimeInForce.GTC,
            intent_id=intent.intent_id,
            created_at=now,
            updated_at=now,
        )

        self._intents_by_order[order.order_id] = intent
        self._adapter.submit_order(order)

    def _handle_order_filled(self, event: OrderFilledEvent) -> None:
        """Processes execution fills, updates the position ledger, and manages bracket orders."""
        order = event.order
        if order is None:
            return

        now = event.timestamp

        # 1. Update the position ledger with the fill
        self._ledger.record_fill(
            symbol=event.symbol,
            side=event.side,
            price=event.fill_price,
            quantity=event.fill_quantity,
            fee=event.fee,
            timestamp=now,
            fee_asset=event.fee_asset,
        )

        # 2. If this was an entry fill, attach bracket Stop-Loss and Take-Profit
        if order.action_purpose == OrderActionPurpose.NEW_ENTRY:
            intent = self._intents_by_order.get(order.order_id)
            if intent is None:
                return

            exit_side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
            children: list[UUID] = []

            # Bracket Child 1: Protective Stop-Loss
            if intent.stop_loss > ZERO_DECIMAL:
                stop_order = Order(
                    symbol=intent.symbol,
                    side=exit_side,
                    order_type=OrderType.STOP,
                    price=intent.stop_loss,
                    quantity=event.fill_quantity,
                    client_order_id=f"STOP-{order.order_id.hex[:8]}",
                    action_purpose=OrderActionPurpose.EXIT,
                    parent_order_id=order.order_id,
                    created_at=now,
                    updated_at=now,
                )
                self._parent_by_child[stop_order.order_id] = order.order_id
                children.append(stop_order.order_id)
                self._active_stop_by_entry[order.order_id] = stop_order
                self._entry_by_symbol[order.symbol] = order.order_id
                self._peak_prices[order.symbol] = event.fill_price
                self._adapter.submit_order(stop_order)

            # Bracket Child 2: Take-Profit Target
            if intent.take_profit > ZERO_DECIMAL:
                tp_order = Order(
                    symbol=intent.symbol,
                    side=exit_side,
                    order_type=OrderType.LIMIT_MAKER,
                    price=intent.take_profit,
                    quantity=event.fill_quantity,
                    client_order_id=f"TP-{order.order_id.hex[:8]}",
                    action_purpose=OrderActionPurpose.EXIT,
                    parent_order_id=order.order_id,
                    created_at=now,
                    updated_at=now,
                )
                self._parent_by_child[tp_order.order_id] = order.order_id
                children.append(tp_order.order_id)
                self._adapter.submit_order(tp_order)

            self._bracket_children[order.order_id] = children

        # 3. If an EXIT order was filled, cancel the other sibling bracket order (OCO)
        elif order.action_purpose in (OrderActionPurpose.EXIT, OrderActionPurpose.RISK_REDUCING):
            if order.price > ZERO_DECIMAL and order.order_type == OrderType.STOP:
                slippage = abs(event.fill_price - order.price) / order.price
                self._slippage_records.append(slippage)

            parent_id = self._parent_by_child.get(order.order_id)
            if parent_id is not None:
                siblings = self._bracket_children.get(parent_id, [])
                for sibling_id in siblings:
                    if sibling_id != order.order_id:
                        self._adapter.cancel_order(sibling_id)
                self._active_stop_by_entry.pop(parent_id, None)
                self._entry_by_symbol.pop(order.symbol, None)
                self._peak_prices.pop(order.symbol, None)

    def check_stale_entry_orders(self) -> int:
        """Cancels unfilled speculative limit entry orders older than entry_timeout_seconds."""
        now = self._clock.now()
        canceled = 0
        for order in self._adapter.get_active_orders():
            if order.action_purpose == OrderActionPurpose.NEW_ENTRY and order.is_active:
                age = (now - order.created_at).total_seconds()
                if age >= self.entry_timeout_seconds:
                    if self._adapter.cancel_order(order.order_id):
                        self._intents_by_order.pop(order.order_id, None)
                        canceled += 1
        return canceled

    def audit_unshielded_positions(self) -> list[str]:
        """Verifies that all open positions have active protective stop-loss orders.

        If an open position lacks a verified stop loss order, automatically submits
        an emergency market exit order to protect capital from unshielded wipeout.
        """
        open_positions = self._ledger.get_open_positions()
        flattened_symbols: list[str] = []
        now = self._clock.now()

        active_orders = self._adapter.get_active_orders()
        active_stop_symbols = {
            o.symbol for o in active_orders if o.order_type == OrderType.STOP and o.is_active
        }

        for pos in open_positions:
            if not pos.is_open:
                continue

            if pos.symbol not in active_stop_symbols:
                exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
                flatten_order = Order(
                    symbol=pos.symbol,
                    side=exit_side,
                    order_type=OrderType.MARKET,
                    price=pos.mark_price,
                    quantity=pos.quantity,
                    client_order_id=f"ORPHAN-SHIELD-{pos.symbol}",
                    action_purpose=OrderActionPurpose.EXIT,
                    created_at=now,
                    updated_at=now,
                )
                self._adapter.submit_order(flatten_order)
                flattened_symbols.append(pos.symbol)

        return flattened_symbols

    def _handle_quote_updated(self, event: QuoteUpdatedEvent) -> None:
        """Freqtrade-inspired dynamic breakeven and trailing stop ratchet."""
        # 1. Adverse selection check: cancel stale unfilled limit entry orders
        self.check_stale_entry_orders()

        # 2. Orphan watchdog: emergency flatten unshielded open positions
        if self.enable_orphan_watchdog:
            self.audit_unshielded_positions()

        if not self.enable_trailing_stop:
            return

        quote = event.quote
        if quote is None:
            return

        entry_order_id = self._entry_by_symbol.get(quote.symbol)
        if entry_order_id is None:
            return

        pos = self._ledger.get_position(quote.symbol)
        if pos is None or not pos.is_open:
            self._entry_by_symbol.pop(quote.symbol, None)
            self._peak_prices.pop(quote.symbol, None)
            self._active_stop_by_entry.pop(entry_order_id, None)
            return

        stop_order = self._active_stop_by_entry.get(entry_order_id)
        if stop_order is None or not stop_order.is_active:
            return

        current_price = quote.mid_price
        entry_price = pos.average_entry_price
        if entry_price <= ZERO_DECIMAL or current_price <= ZERO_DECIMAL:
            return

        candidate_stop: Decimal | None = None

        if pos.side == PositionSide.LONG:
            peak = max(self._peak_prices.get(quote.symbol, entry_price), current_price)
            self._peak_prices[quote.symbol] = peak

            profit_ratio = (current_price - entry_price) / entry_price

            # 1. Breakeven Lock (+0.75% profit -> Lock Entry + 0.08% fee buffer)
            if profit_ratio >= self.breakeven_offset_ratio:
                be_price = entry_price * (Decimal("1.0") + self.breakeven_fee_buffer)
                if be_price > stop_order.price:
                    candidate_stop = be_price

            # 2. Dynamic Trailing Stop (+1.20% profit -> Trail 0.5% behind peak)
            if profit_ratio >= self.trailing_offset_ratio:
                trail_price = peak * (Decimal("1.0") - self.trailing_delta_ratio)
                if candidate_stop is None or trail_price > candidate_stop:
                    if trail_price > stop_order.price:
                        candidate_stop = trail_price

            if candidate_stop is not None and candidate_stop > stop_order.price:
                delta_ratio = (candidate_stop - stop_order.price) / stop_order.price
                if delta_ratio >= self.trailing_min_ratchet_pct:
                    self._ratchet_stop_loss(entry_order_id, stop_order, candidate_stop)

        elif pos.side == PositionSide.SHORT:
            peak = min(self._peak_prices.get(quote.symbol, entry_price), current_price)
            self._peak_prices[quote.symbol] = peak

            profit_ratio = (entry_price - current_price) / entry_price

            # 1. Breakeven Lock (+0.75% profit -> Lock Entry - 0.08% fee buffer)
            if profit_ratio >= self.breakeven_offset_ratio:
                be_price = entry_price * (Decimal("1.0") - self.breakeven_fee_buffer)
                if be_price < stop_order.price:
                    candidate_stop = be_price

            # 2. Dynamic Trailing Stop (+1.20% profit -> Trail 0.5% behind peak)
            if profit_ratio >= self.trailing_offset_ratio:
                trail_price = peak * (Decimal("1.0") + self.trailing_delta_ratio)
                if candidate_stop is None or trail_price < candidate_stop:
                    if trail_price < stop_order.price:
                        candidate_stop = trail_price

            if candidate_stop is not None and candidate_stop < stop_order.price:
                delta_ratio = (stop_order.price - candidate_stop) / stop_order.price
                if delta_ratio >= self.trailing_min_ratchet_pct:
                    self._ratchet_stop_loss(entry_order_id, stop_order, candidate_stop)

    def _ratchet_stop_loss(
        self, entry_order_id: UUID, old_stop_order: Order, new_stop_price: Decimal
    ) -> Order:
        """Cancels existing stop loss and submits a ratcheted protective stop order."""
        self._adapter.cancel_order(old_stop_order.order_id)
        now = self._clock.now()
        new_stop = Order(
            symbol=old_stop_order.symbol,
            side=old_stop_order.side,
            order_type=OrderType.STOP,
            price=new_stop_price,
            quantity=old_stop_order.quantity,
            client_order_id=f"TRAIL-{entry_order_id.hex[:8]}-{int(now.timestamp())}",
            action_purpose=OrderActionPurpose.EXIT,
            parent_order_id=entry_order_id,
            created_at=now,
            updated_at=now,
        )
        self._parent_by_child[new_stop.order_id] = entry_order_id
        if entry_order_id in self._bracket_children:
            children = self._bracket_children[entry_order_id]
            if old_stop_order.order_id in children:
                children.remove(old_stop_order.order_id)
            children.append(new_stop.order_id)

        self._active_stop_by_entry[entry_order_id] = new_stop
        self._adapter.submit_order(new_stop)
        return new_stop

    def _handle_session_state_changed(self, event: TradingSessionStateChangedEvent) -> None:
        """Handles emergency state transitions by canceling entries and flattening positions."""
        if event.new_state == SessionState.EMERGENCY_STOP:
            self.cancel_all_speculative_orders()
            self.flatten_all_positions()

    def cancel_all_speculative_orders(self) -> int:
        """Cancels all active speculative new entry orders."""
        active = self._adapter.get_active_orders()
        canceled_count = 0
        for order in active:
            if order.action_purpose == OrderActionPurpose.NEW_ENTRY:
                if self._adapter.cancel_order(order.order_id):
                    canceled_count += 1
        return canceled_count

    def flatten_all_positions(self) -> list[Order]:
        """Submits immediate market exit orders for all open positions."""
        open_positions = self._ledger.get_open_positions()
        exit_orders: list[Order] = []
        now = self._clock.now()

        for pos in open_positions:
            exit_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
            flatten_order = Order(
                symbol=pos.symbol,
                side=exit_side,
                order_type=OrderType.MARKET,
                price=pos.mark_price,
                quantity=pos.quantity,
                client_order_id=f"FLATTEN-{pos.symbol}",
                action_purpose=OrderActionPurpose.EXIT,
                created_at=now,
                updated_at=now,
            )
            self._adapter.submit_order(flatten_order)
            exit_orders.append(flatten_order)

        return exit_orders
