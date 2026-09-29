"""Unit tests for RiskGatekeeper financial limit enforcements and intent creation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import (
    OrderSide,
    OrderType,
    RiskDecisionType,
    SessionState,
    TradingMode,
)
from trad_auto.core.events import (
    DailyProfitTargetReachedEvent,
    RiskLimitBreachedEvent,
    TradeIntentCreatedEvent,
    TradeProposalRejectedEvent,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.intent import TradeIntent
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.risk.gatekeeper import RiskGatekeeper
from trad_auto.risk.ledger_bridge import InMemoryPortfolioRiskBridge


def create_instrument(
    symbol: str = "BTCUSDT",
    is_tradable: bool = True,
    step: Decimal = Decimal("0.001"),
    min_qty: Decimal = Decimal("0.001"),
) -> Instrument:
    return Instrument(
        symbol=symbol,
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=Decimal("0.1"),
        lot_size=step,
        quantity_step=step,
        min_quantity=min_qty,
        is_tradable=is_tradable,
    )


def create_proposal(
    symbol: str = "BTCUSDT",
    entry: str = "60000.00",
    stop: str = "59000.00",
    target: str = "62000.00",
    direction: OrderSide = OrderSide.BUY,
    timestamp: datetime | None = None,
) -> TradeProposal:
    return TradeProposal(
        strategy_id="strat_test",
        symbol=symbol,
        timeframe="1h",
        direction=direction,
        entry_price=Decimal(entry),
        stop_loss=Decimal(stop),
        take_profit=Decimal(target),
        timestamp=timestamp or datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        reason="test trigger",
    )


def test_risk_gatekeeper_approval_happy_path() -> None:
    """Verifies that a valid proposal in TRADING mode is approved and creates a TradeIntent."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument()

    # Start active trading session with $10,000 authorized capital
    limits = FinancialLimits(
        authorized_capital=Decimal("10000.00"),
        max_risk_amount=Decimal("100.00"),
        max_exposure=Decimal("10000.00"),
        max_allowed_loss=Decimal("500.00"),
    )
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)

    intents_created: list[TradeIntent] = []
    bus.subscribe(
        TradeIntentCreatedEvent,
        lambda e: intents_created.append(e.intent) if e.intent else None,
    )

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
        default_risk_pct=Decimal("0.01"),  # 1% risk = $100 budget
    )

    # Proposal: Entry = 60000, Stop = 59000 (Risk per unit = 1000)
    # Budget = 100. Sized Qty = 100 / 1000 = 0.1 BTC
    proposal = create_proposal()
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.APPROVED
    assert result.calculated_quantity == Decimal("0.100")
    assert len(intents_created) == 1

    intent = intents_created[0]
    assert intent.symbol == "BTCUSDT"
    assert intent.quantity == Decimal("0.100")
    assert intent.order_type == OrderType.LIMIT_MAKER
    assert intent.stop_loss == Decimal("59000.00")
    assert intent.take_profit == Decimal("62000.00")


def test_risk_gatekeeper_rejects_inactive_session_state() -> None:
    """Verifies that gatekeeper rejects proposals if session is IDLE or PAUSED."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)  # Starts in IDLE
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument()

    rejected_events: list[str] = []
    bus.subscribe(
        TradeProposalRejectedEvent,
        lambda e: rejected_events.append(e.reason),
    )

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
    )

    proposal = create_proposal()
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "not authorized" in result.reason
    assert len(rejected_events) == 1


def test_risk_gatekeeper_daily_loss_limit_drawdown_breaker() -> None:
    """Verifies that breaching max_allowed_loss transitions session to RISK_LOCKED."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument()

    limits = FinancialLimits(
        authorized_capital=Decimal("1000.00"),
        max_allowed_loss=Decimal("50.00"),  # $50 max daily loss
    )
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)
    assert mgr.get_state() == SessionState.TRADING

    limit_breach_events: list[str] = []
    bus.subscribe(
        RiskLimitBreachedEvent,
        lambda e: limit_breach_events.append(e.limit_type),
    )

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
    )

    # Set portfolio daily loss to -$60 (> $50 limit)
    bridge.set_today_realized_pnl(Decimal("-60.00"))

    proposal = create_proposal()
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "Daily loss limit breached" in result.reason
    assert mgr.get_state() == SessionState.RISK_LOCKED
    assert "DAILY_LOSS" in limit_breach_events


def test_risk_gatekeeper_daily_profit_target_protection() -> None:
    """Verifies that reaching daily profit target pauses trading to lock accumulated gains."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument()

    limits = FinancialLimits(
        authorized_capital=Decimal("1000.00"),
        daily_profit_target=Decimal("100.00"),  # $100 profit target
    )
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)

    profit_events: list[Decimal] = []
    bus.subscribe(
        DailyProfitTargetReachedEvent,
        lambda e: profit_events.append(e.realized_profit),
    )

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
    )

    # Bridge reports $120 realized profit today
    bridge.set_today_realized_pnl(Decimal("120.00"))

    proposal = create_proposal()
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "profit target reached" in result.reason
    assert mgr.get_state() == SessionState.PAUSED
    assert profit_events == [Decimal("120.00")]


def test_risk_gatekeeper_gross_exposure_limit() -> None:
    """Verifies that orders pushing gross exposure beyond max_exposure are rejected."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument()

    limits = FinancialLimits(
        authorized_capital=Decimal("1000.00"),
        max_exposure=Decimal("6000.00"),  # $6,000 max exposure
    )
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)

    # Current open exposure is $5,800
    bridge.set_exposure("ETHUSDT", Decimal("5800.00"))

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
        default_risk_pct=Decimal("0.10"),  # $100 risk -> 0.1 BTC notional = $6,000
    )

    # New trade notional = 0.1 * 60000 = $6,000. Total = 5800 + 6000 = $11,800 > $6,000 limit
    proposal = create_proposal()
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "pushes total exposure" in result.reason


def test_risk_gatekeeper_untradable_instrument() -> None:
    """Verifies rejection if instrument is flagged is_tradable=False."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument(is_tradable=False)

    limits = FinancialLimits(authorized_capital=Decimal("1000.00"))
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
    )

    proposal = create_proposal()
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "not tradable" in result.reason


def test_risk_gatekeeper_blocks_on_stale_feed() -> None:
    """Verifies that trade proposal is rejected fail-closed if market data feed is stale."""
    from trad_auto.market_data.streaming.watchdog import FeedWatchdog

    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    inst = create_instrument()

    limits = FinancialLimits(authorized_capital=Decimal("1000.00"))
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)

    # Watchdog timeout 15s, last activity 30s ago -> STALE
    watchdog = FeedWatchdog(timeout_seconds=15.0, event_bus=bus)
    watchdog.track_symbol("BTCUSDT")
    watchdog.record_activity("BTCUSDT", timestamp=t0)

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        instruments={"BTCUSDT": inst},
        feed_watchdog=watchdog,
    )

    # Proposal arrives at t0 + 20s (> 15s timeout)
    t_stale = t0 + timedelta(seconds=20)
    proposal = create_proposal(timestamp=t_stale)
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "STALE" in result.reason
