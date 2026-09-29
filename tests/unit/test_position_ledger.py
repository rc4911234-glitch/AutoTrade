"""Unit and integration tests for PositionLedger and LedgerPortfolioRiskBridge."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import (
    OrderSide,
    PositionSide,
    RiskDecisionType,
    SessionState,
    TradingMode,
)
from trad_auto.core.events import (
    BalanceUpdatedEvent,
    FundingPaymentEvent,
    FundingRateEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    PositionUpdatedEvent,
    QuoteUpdatedEvent,
    RiskLimitBreachedEvent,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.market_data import FundingRate, Quote
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.time import SimulatedClock
from trad_auto.portfolio.ledger import PositionLedger
from trad_auto.risk.gatekeeper import RiskGatekeeper
from trad_auto.risk.real_bridge import LedgerPortfolioRiskBridge


def create_test_instrument(symbol: str = "BTCUSDT") -> Instrument:
    return Instrument(
        symbol=symbol,
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )


def test_position_ledger_initial_balance_and_deposits() -> None:
    bus = EventBus()
    events: list[BalanceUpdatedEvent] = []
    bus.subscribe(BalanceUpdatedEvent, lambda e: events.append(e))

    ledger = PositionLedger(event_bus=bus)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    bal = ledger.get_balance("USDT")
    assert bal.free == Decimal("1000.00")
    assert bal.locked == ZERO_DECIMAL
    assert bal.total == Decimal("1000.00")
    assert len(events) == 1

    ledger.deposit("USDT", Decimal("500.00"))
    assert bal.free == Decimal("1500.00")
    assert len(events) == 2

    ledger.withdraw("USDT", Decimal("200.00"))
    assert bal.free == Decimal("1300.00")
    assert len(events) == 3


def test_position_ledger_open_long_position() -> None:
    bus = EventBus()
    opened_events: list[PositionOpenedEvent] = []
    bus.subscribe(PositionOpenedEvent, lambda e: opened_events.append(e))

    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    # Buy 0.5 BTC at $100.00 with $0.10 fee
    t0 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("0.5"),
        fee=Decimal("0.10"),
        timestamp=t0,
    )

    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.is_open
    assert pos.side == PositionSide.LONG
    assert pos.quantity == Decimal("0.5")
    assert pos.average_entry_price == Decimal("100.00")
    assert pos.mark_price == Decimal("100.00")
    assert pos.unrealized_pnl == ZERO_DECIMAL

    # Fee deducted from USDT
    bal = ledger.get_balance("USDT")
    assert bal.free == Decimal("999.90")
    assert len(opened_events) == 1
    assert opened_events[0].symbol == "BTCUSDT"


def test_position_ledger_scale_in_long_position() -> None:
    bus = EventBus()
    updated_events: list[PositionUpdatedEvent] = []
    bus.subscribe(PositionUpdatedEvent, lambda e: updated_events.append(e))

    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    t0 = clock.now()
    # Lot 1: 1.0 @ 100
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("1.0"),
        fee=Decimal("0.10"),
        timestamp=t0,
    )

    # Lot 2: 1.0 @ 200 (VWAP should become 150.00)
    clock.advance(timedelta(minutes=1))
    t1 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("200.00"),
        quantity=Decimal("1.0"),
        fee=Decimal("0.10"),
        timestamp=t1,
    )

    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.quantity == Decimal("2.0")
    assert pos.average_entry_price == Decimal("150.00")
    assert len(updated_events) == 1


def test_position_ledger_mark_to_market_quote_updates() -> None:
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    # Open Long 1.0 BTC @ 100
    t0 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("1.0"),
        fee=ZERO_DECIMAL,
        timestamp=t0,
    )

    # Price moves to 125.00 (Unrealized PnL should be +25.00)
    clock.advance(timedelta(seconds=5))
    t1 = clock.now()
    quote = Quote(
        symbol="BTCUSDT",
        bid_price=Decimal("124.90"),
        ask_price=Decimal("125.10"),
        bid_size=Decimal("10.0"),
        ask_size=Decimal("10.0"),
        timestamp=t1,
    )
    bus.publish(QuoteUpdatedEvent(quote=quote))

    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.mark_price == Decimal("125.00")
    assert pos.unrealized_pnl == Decimal("25.00")
    assert ledger.get_current_unrealized_pnl() == Decimal("25.00")


def test_position_ledger_close_position_with_realized_profit() -> None:
    bus = EventBus()
    closed_events: list[PositionClosedEvent] = []
    bus.subscribe(PositionClosedEvent, lambda e: closed_events.append(e))

    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    # Open Long 1.0 @ 100
    t0 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("1.0"),
        fee=Decimal("1.00"),
        timestamp=t0,
    )

    # Close Long 1.0 @ 150 with $1 fee
    # Gross PnL: 50.00, Exit fee: 1.00 -> Net Realized PnL: 49.00
    clock.advance(timedelta(minutes=10))
    t1 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        price=Decimal("150.00"),
        quantity=Decimal("1.0"),
        fee=Decimal("1.00"),
        timestamp=t1,
    )

    assert ledger.get_position("BTCUSDT") is None
    assert len(closed_events) == 1
    assert closed_events[0].realized_pnl == Decimal("49.00")

    # Balances: Initial 1000 - 1.00 (entry fee) - 1.00 (exit fee) + 50.00 (gross profit) = 1048.00
    # Net: 1000 - 1.00 (fee) + 49.00 (net pnl) - 1.00 (fee) = 1048.00 - wait:
    # record_fill deducts fee (1.00) and credits net realized pnl (49.00 = 50 gross - 1.00 exit fee)
    # Total USDT = 1000 - 1 (entry fee) - 1 (exit fee) + 49 (net pnl) = 1047.00
    # Let's verify exact balance
    bal = ledger.get_balance("USDT")
    assert bal.free == Decimal("1047.00")
    assert ledger.get_today_realized_pnl(current_date=t1.date()) == Decimal("49.00")


def test_position_ledger_reversal_from_long_to_short() -> None:
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    # Open Long 1.0 @ 100
    t0 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("1.0"),
        fee=ZERO_DECIMAL,
        timestamp=t0,
    )

    # Reversal: SELL 2.5 @ 120
    # Closes 1.0 Long (Realized PnL = +20.00)
    # Opens 1.5 Short @ 120
    clock.advance(timedelta(minutes=5))
    t1 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        price=Decimal("120.00"),
        quantity=Decimal("2.5"),
        fee=ZERO_DECIMAL,
        timestamp=t1,
    )

    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.is_open
    assert pos.side == PositionSide.SHORT
    assert pos.quantity == Decimal("1.5")
    assert pos.average_entry_price == Decimal("120.00")
    assert ledger.get_today_realized_pnl(current_date=t1.date()) == Decimal("20.00")


def test_position_ledger_perpetual_funding_rate_payment() -> None:
    bus = EventBus()
    funding_events: list[FundingPaymentEvent] = []
    bus.subscribe(FundingPaymentEvent, lambda e: funding_events.append(e))

    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    # Open Long 2.0 @ 100 (Notional = 200)
    t0 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("2.0"),
        fee=ZERO_DECIMAL,
        timestamp=t0,
    )

    # Positive funding rate 0.01 (1%): Longs pay Shorts
    # Funding Payment = 2.0 * 100 * 0.01 = $2.00 (debit from USDT)
    rate_event = FundingRateEvent(
        funding_rate=FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.01"),
            timestamp=clock.now(),
            next_funding_time=clock.now() + timedelta(hours=8),
        )
    )
    bus.publish(rate_event)

    bal = ledger.get_balance("USDT")
    assert bal.free == Decimal("998.00")
    assert len(funding_events) == 1
    assert funding_events[0].payment_amount == Decimal("-2.00")


def test_ledger_portfolio_risk_bridge_integration_with_gatekeeper() -> None:
    """Verifies that RiskGatekeeper trips circuit breaker using live ledger metrics."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    session_mgr = TradingSessionManager(bus)
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    bridge = LedgerPortfolioRiskBridge(ledger)
    gatekeeper = RiskGatekeeper(event_bus=bus, session_manager=session_mgr, portfolio_bridge=bridge)
    inst = create_test_instrument()
    gatekeeper.register_instrument(inst)

    limits = FinancialLimits(
        authorized_capital=Decimal("1000.00"),
        max_allowed_loss=Decimal("50.00"),  # $50 max daily loss threshold
    )
    t0 = clock.now()
    session_mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)
    assert session_mgr.get_state() == SessionState.TRADING

    # Create a losing trade in ledger:
    # Buy 1.0 @ 100, Sell 1.0 @ 40 -> Realized Loss = -$60.00 (exceeds $50 limit)
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("1.0"),
        fee=ZERO_DECIMAL,
        timestamp=t0,
    )
    clock.advance(timedelta(minutes=1))
    t1 = clock.now()
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        price=Decimal("40.00"),
        quantity=Decimal("1.0"),
        fee=ZERO_DECIMAL,
        timestamp=t1,
    )

    breach_events: list[RiskLimitBreachedEvent] = []
    bus.subscribe(RiskLimitBreachedEvent, lambda e: breach_events.append(e))

    # Next proposal is evaluated:
    prop = TradeProposal(
        strategy_id="strat1",
        symbol="BTCUSDT",
        timeframe="1h",
        direction=OrderSide.BUY,
        entry_price=Decimal("100.00"),
        stop_loss=Decimal("95.00"),
        take_profit=Decimal("115.00"),
        timestamp=t1,
        reason="Test breakout",
    )

    res = gatekeeper.evaluate_proposal(prop)
    assert res.decision == RiskDecisionType.REJECTED
    assert "Daily loss limit breached" in res.reason

    # State machine must be locked in RISK_LOCKED!
    assert session_mgr.get_state() == SessionState.RISK_LOCKED
    assert len(breach_events) == 1
