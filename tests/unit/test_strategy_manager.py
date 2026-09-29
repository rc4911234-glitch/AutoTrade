"""Unit tests for StrategyManager event routing and session state authorization."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import OrderSide, SessionState
from trad_auto.core.events import BarCompletedEvent, QuoteUpdatedEvent, TradeProposalEvent
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.base import BaseStrategy
from trad_auto.strategies.manager import StrategyManager


class MockDummyStrategy(BaseStrategy):
    """Deterministic dummy strategy that emits a fixed proposal on every bar."""

    def __init__(self, strategy_id: str = "dummy_1") -> None:
        super().__init__(strategy_id, symbols=["BTCUSDT"], timeframes=["1h"])

    def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
        return [
            TradeProposal(
                strategy_id=self.strategy_id,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                direction=OrderSide.BUY,
                entry_price=Decimal("100"),
                stop_loss=Decimal("90"),
                take_profit=Decimal("120"),
                timestamp=bar.timestamp,
                reason="mock trigger",
            )
        ]

    def reset(self) -> None:
        pass


def test_strategy_manager_lifecycle_and_lookup() -> None:
    """Verifies strategy registration, duplicate prevention, enable/disable, and removal."""
    bus = EventBus()
    store = BarStore()
    mgr = StrategyManager(bus, store)

    strat = MockDummyStrategy("strat_a")
    mgr.register_strategy(strat)

    assert mgr.get_strategy("strat_a") == strat
    assert len(mgr.list_strategies()) == 1

    # Duplicate registration fails
    with pytest.raises(DomainValidationError, match="already registered"):
        mgr.register_strategy(strat)

    # Enable and disable
    mgr.disable_strategy("strat_a")
    assert not strat.is_enabled
    mgr.enable_strategy("strat_a")
    assert strat.is_enabled

    # Unregister
    mgr.unregister_strategy("strat_a")
    assert mgr.get_strategy("strat_a") is None
    assert len(mgr.list_strategies()) == 0


def test_strategy_manager_session_state_authorization_guardrail() -> None:
    """Cardinal Rule: If session state != TRADING, strategy proposals must be suppressed."""
    bus = EventBus()
    store = BarStore()
    current_state = SessionState.IDLE

    # Strategy manager tied to dynamic session state provider
    mgr = StrategyManager(bus, store, session_state_provider=lambda: current_state)
    mgr.register_strategy(MockDummyStrategy("strat_live"))

    proposals_emitted: list[TradeProposal] = []
    bus.subscribe(
        TradeProposalEvent,
        lambda e: proposals_emitted.append(e.proposal) if e.proposal else None,
    )

    bar = Bar(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("95"),
        close=Decimal("100"),
        volume=Decimal("10"),
    )

    # 1. State is IDLE -> Proposals MUST be suppressed!
    current_state = SessionState.IDLE
    assert not mgr.is_trading_allowed()
    bus.publish(BarCompletedEvent(bar=bar))
    assert len(proposals_emitted) == 0

    # 2. State is PAUSED -> Proposals MUST be suppressed!
    current_state = SessionState.PAUSED
    assert not mgr.is_trading_allowed()
    bus.publish(BarCompletedEvent(bar=bar))
    assert len(proposals_emitted) == 0

    # 3. State is EMERGENCY_STOP -> Proposals MUST be suppressed!
    current_state = SessionState.EMERGENCY_STOP
    assert not mgr.is_trading_allowed()
    bus.publish(BarCompletedEvent(bar=bar))
    assert len(proposals_emitted) == 0

    # 4. State is TRADING -> Authorized! Proposals flow through to event bus
    current_state = SessionState.TRADING
    assert mgr.is_trading_allowed()
    bus.publish(BarCompletedEvent(bar=bar))
    assert len(proposals_emitted) == 1
    assert proposals_emitted[0].strategy_id == "strat_live"
    assert proposals_emitted[0].direction == OrderSide.BUY


def test_strategy_manager_quote_handling() -> None:
    """Verifies that StrategyManager routes quote updates to registered strategies."""
    bus = EventBus()
    store = BarStore()
    mgr = StrategyManager(bus, store, session_state_provider=lambda: SessionState.TRADING)

    class MockQuoteStrategy(BaseStrategy):
        def __init__(self) -> None:
            super().__init__("strat_quote", symbols=["BTCUSDT"], timeframes=["1h"])

        def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
            return []

        def on_quote(self, quote: Quote) -> list[TradeProposal]:
            return [
                TradeProposal(
                    strategy_id=self.strategy_id,
                    symbol=quote.symbol,
                    timeframe="1h",
                    direction=OrderSide.BUY,
                    entry_price=quote.ask_price,
                    stop_loss=quote.ask_price - Decimal("10"),
                    take_profit=quote.ask_price + Decimal("20"),
                    timestamp=quote.timestamp,
                    reason="quote trigger",
                )
            ]

        def reset(self) -> None:
            pass

    mgr.register_strategy(MockQuoteStrategy())

    proposals: list[TradeProposal] = []
    bus.subscribe(
        TradeProposalEvent,
        lambda e: proposals.append(e.proposal) if e.proposal else None,
    )

    quote = Quote(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        bid_price=Decimal("100"),
        ask_price=Decimal("101"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    bus.publish(QuoteUpdatedEvent(quote=quote))
    assert len(proposals) == 1
    assert proposals[0].entry_price == Decimal("101")
