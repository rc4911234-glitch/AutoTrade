"""Unit tests for NewsVolatilityShield, RiskGatekeeper news filtering, and telemetry."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import (
    NewsImpactLevel,
    OrderSide,
    RiskDecisionType,
    TradingMode,
)
from trad_auto.core.events import (
    NewsArticleReceivedEvent,
    NewsShieldBlackoutEngagedEvent,
    TradeProposalRejectedEvent,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.news import MacroEconomicEvent, NewsArticle
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.time import SimulatedClock
from trad_auto.engine import TradingEngine
from trad_auto.health import ComponentStatus, HealthMonitor
from trad_auto.news.calendar import MacroCalendarManager
from trad_auto.news.shield import NewsVolatilityShield
from trad_auto.risk.gatekeeper import RiskGatekeeper
from trad_auto.risk.ledger_bridge import InMemoryPortfolioRiskBridge


def create_instrument(symbol: str = "BTCUSDT") -> Instrument:
    return Instrument(
        symbol=symbol,
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.001"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        is_tradable=True,
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
        timestamp=timestamp or datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        reason="test trigger",
    )


def test_news_volatility_shield_article_ingestion_and_blackout() -> None:
    """Verifies article ingestion, sentiment scoring, and critical breaking blackout engagement."""
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    bus = EventBus()

    received_events: list[NewsArticleReceivedEvent] = []
    blackout_events: list[NewsShieldBlackoutEngagedEvent] = []
    bus.subscribe(NewsArticleReceivedEvent, received_events.append)
    bus.subscribe(NewsShieldBlackoutEngagedEvent, blackout_events.append)

    shield = NewsVolatilityShield(
        event_bus=bus,
        clock=clock,
        breaking_news_blackout_minutes=30,
    )

    # Ingest routine bullish news
    bull_article = NewsArticle(
        title="Institutional inflows surge as new spot Bitcoin ETF launched",
        source="Bloomberg",
        published_at=t0,
        symbols=["BTCUSDT"],
    )
    res_bull = shield.ingest_article(bull_article)
    assert res_bull.sentiment_score > Decimal("0.30")
    assert len(received_events) == 1
    assert len(blackout_events) == 0
    assert not shield.is_blackout_active("BTCUSDT")

    # Ingest catastrophic critical news
    hack_article = NewsArticle(
        title="Major cross-chain protocol hacked and exploited, liquidity insolvent",
        source="Reuters",
        published_at=t0,
        symbols=["BTCUSDT"],
    )
    res_crit = shield.ingest_article(hack_article)
    assert res_crit.impact == NewsImpactLevel.CRITICAL
    assert len(received_events) == 2
    assert len(blackout_events) == 1
    assert blackout_events[0].title == hack_article.title
    assert blackout_events[0].blackout_end == t0 + timedelta(minutes=30)

    # Blackout is now active
    assert shield.is_blackout_active("BTCUSDT") is True

    # Advance clock past 30 minutes
    clock.advance(timedelta(minutes=31))
    assert shield.is_blackout_active("BTCUSDT") is False


def test_news_shield_sentiment_alignment_rejections() -> None:
    """Tests technical proposals being blocked when opposing prevailing extreme news."""
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    shield = NewsVolatilityShield(
        clock=clock,
        enforce_sentiment_alignment=True,
        sentiment_rejection_threshold=Decimal("0.60"),
    )

    # Ingest strongly negative news (HIGH impact, score -0.75, below CRITICAL threshold 0.85)
    bad_article = NewsArticle(
        title="Regulators announce nationwide crypto crackdown and enforcement",
        source="WSJ",
        published_at=t0,
        symbols=["BTCUSDT"],
    )
    res_bad = shield.ingest_article(bad_article)
    assert res_bad.impact == NewsImpactLevel.HIGH
    assert not shield.is_blackout_active("BTCUSDT")

    # Technical BUY proposal opposes severe bearish news (-0.60+)
    buy_prop = create_proposal(direction=OrderSide.BUY, timestamp=t0)
    allowed, reason = shield.check_trade_proposal(buy_prop, now=t0)
    assert not allowed
    assert "conflicts with severe negative news sentiment" in reason

    # Technical SELL proposal with bearish news is aligned and allowed
    sell_prop = create_proposal(
        direction=OrderSide.SELL,
        entry="60000.00",
        stop="61000.00",
        target="58000.00",
        timestamp=t0,
    )
    allowed_sell, _ = shield.check_trade_proposal(sell_prop, now=t0)
    assert allowed_sell is True

    # Advance clock past 60-minute rolling window so bearish article expires
    clock.advance(timedelta(minutes=61))
    t1 = clock.now()

    # Ingest strongly bullish news (HIGH impact, score +0.75)
    good_article = NewsArticle(
        title="Government approves treasury reserve allocation for digital assets",
        source="Bloomberg",
        published_at=t1,
        symbols=["BTCUSDT"],
    )
    res_good = shield.ingest_article(good_article)
    assert res_good.impact == NewsImpactLevel.HIGH

    # Technical SELL proposal opposes severe bullish news (+0.75)
    sell_prop_t1 = create_proposal(
        direction=OrderSide.SELL,
        entry="60000.00",
        stop="61000.00",
        target="58000.00",
        timestamp=t1,
    )
    allowed_opp_sell, reason_opp = shield.check_trade_proposal(sell_prop_t1, now=t1)
    assert not allowed_opp_sell
    assert "conflicts with severe positive news sentiment" in reason_opp

    # When enforcement is disabled, trades proceed
    shield.enforce_sentiment_alignment = False
    allowed_disabled, _ = shield.check_trade_proposal(sell_prop_t1, now=t1)
    assert allowed_disabled is True


def test_risk_gatekeeper_blocks_trade_during_macro_blackout() -> None:
    """Verifies that RiskGatekeeper rejects trade proposals during active macroeconomic releases."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    # Setup calendar with US CPI release at 12:10 UTC (blackout 11:55 - 12:25)
    cal = MacroCalendarManager()
    cpi_event = MacroEconomicEvent(
        title="US Consumer Price Index (CPI)",
        scheduled_at=t0 + timedelta(minutes=10),
        cool_off_pre_minutes=15,
        cool_off_post_minutes=15,
        affected_symbols=["*"],
    )
    cal.register_event(cpi_event)

    shield = NewsVolatilityShield(calendar_mgr=cal)

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        news_shield=shield,
    )
    gatekeeper.register_instrument(create_instrument("BTCUSDT"))

    # Start authorized trading session
    limits = FinancialLimits(
        authorized_capital=Decimal("10000.00"), max_risk_amount=Decimal("100.00")
    )
    mgr.activate_session(TradingMode.PAPER, limits, uuid4(), t0)

    rejections: list[TradeProposalRejectedEvent] = []
    bus.subscribe(TradeProposalRejectedEvent, rejections.append)

    # Evaluate proposal at 12:00 (inside CPI blackout)
    proposal = create_proposal(timestamp=t0)
    result = gatekeeper.evaluate_proposal(proposal)

    assert result.decision == RiskDecisionType.REJECTED
    assert "NewsVolatilityShield: Active macro blackout" in result.reason
    assert len(rejections) == 1
    assert "CPI" in rejections[0].reason

    # Evaluate proposal at 12:30 (outside CPI blackout window)
    t_safe = t0 + timedelta(minutes=30)
    safe_prop = create_proposal(timestamp=t_safe)
    result_safe = gatekeeper.evaluate_proposal(safe_prop)
    assert result_safe.decision == RiskDecisionType.APPROVED


def test_health_monitor_news_shield_telemetry() -> None:
    """Verifies HealthMonitor reflects NewsVolatilityShield status and blackout degradation."""
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    cal = MacroCalendarManager()
    shield = NewsVolatilityShield(calendar_mgr=cal, clock=clock)

    health_mon = HealthMonitor(news_shield=shield, clock=clock)

    # 1. Clear status
    report_clear = health_mon.get_health_report()
    assert report_clear.is_healthy is True
    assert "news_shield" in report_clear.components
    assert report_clear.components["news_shield"].status == ComponentStatus.HEALTHY
    assert report_clear.components["news_shield"].details["blackout_active"] is False

    # 2. Add FOMC event with active blackout at 12:00
    cal.register_event(
        MacroEconomicEvent(
            title="FOMC Rate Decision",
            scheduled_at=t0 + timedelta(minutes=5),
            cool_off_pre_minutes=15,
            cool_off_post_minutes=15,
        )
    )

    # Blackout now active -> status degrades to DEGRADED
    report_blackout = health_mon.get_health_report()
    assert report_blackout.status == ComponentStatus.DEGRADED
    assert report_blackout.components["news_shield"].status == ComponentStatus.DEGRADED
    assert report_blackout.components["news_shield"].details["blackout_active"] is True
    assert (
        report_blackout.components["news_shield"].details["next_macro_event"]
        == "FOMC Rate Decision"
    )


def test_trading_engine_news_shield_integration() -> None:
    """Verifies TradingEngine initializes NewsVolatilityShield and exposes status and health."""
    engine = TradingEngine()
    assert engine.news_shield is not None
    assert engine.risk_gatekeeper.news_shield is engine.news_shield

    status = engine.get_status()
    assert status["news_shield_enabled"] is True

    health = engine.get_health()
    assert "news_shield" in health.components
    assert health.components["news_shield"].status == ComponentStatus.HEALTHY


def test_shield_breaking_news_blackout_rejection() -> None:
    """Verifies that an active breaking news blackout directly rejects proposals."""
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    shield = NewsVolatilityShield(clock=clock, breaking_news_blackout_minutes=30)

    # Ingest critical news triggering flash blackout
    article = NewsArticle(
        title="Major exchange insolvency and immediate bankruptcy freeze",
        source="Reuters",
        published_at=t0,
        symbols=["BTCUSDT"],
    )
    shield.ingest_article(article)
    assert shield.is_blackout_active("BTCUSDT") is True

    # Proposal rejected because of active breaking blackout
    proposal = create_proposal(symbol="BTCUSDT", timestamp=t0)
    allowed, reason = shield.check_trade_proposal(proposal, now=t0)
    assert allowed is False
    assert "Active breaking news blackout" in reason


def test_shield_register_macro_event_and_dynamic_set() -> None:
    """Tests shield register_macro_event and gatekeeper dynamic set_news_shield."""
    bus = EventBus()
    mgr = TradingSessionManager(bus)
    bridge = InMemoryPortfolioRiskBridge()
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    shield = NewsVolatilityShield()
    ev = MacroEconomicEvent(
        title="Fed Powell Press Conference",
        scheduled_at=t0 + timedelta(minutes=5),
    )
    shield.register_macro_event(ev)
    assert shield.is_blackout_active("*", now=t0) is True

    gatekeeper = RiskGatekeeper(
        event_bus=bus,
        session_manager=mgr,
        portfolio_bridge=bridge,
        news_shield=shield,
    )
    assert gatekeeper.news_shield is shield

    # Clear dynamically
    gatekeeper.set_news_shield(None)
    assert gatekeeper.news_shield is None
