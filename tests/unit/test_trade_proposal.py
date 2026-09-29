"""Unit tests for TradeProposal model and mathematical invariants."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.enums import OrderSide
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.strategy import TradeProposal


def test_valid_long_trade_proposal() -> None:
    """Verifies valid Long proposal construction, risk, reward, and 1:2 R:R."""
    now = datetime.now(UTC)
    p = TradeProposal(
        strategy_id="test_strat",
        symbol="BTCUSDT",
        timeframe="1h",
        direction=OrderSide.BUY,
        entry_price=Decimal("60000.00"),
        stop_loss=Decimal("59000.00"),  # Risk = 1000
        take_profit=Decimal("62000.00"),  # Reward = 2000
        timestamp=now,
        reason="Test breakout",
    )
    assert p.risk_amount_per_unit == Decimal("1000.00")
    assert p.reward_amount_per_unit == Decimal("2000.00")
    assert p.risk_reward_ratio == Decimal("2.0")


def test_valid_short_trade_proposal() -> None:
    """Verifies valid Short proposal construction, risk, reward, and 1:2 R:R."""
    now = datetime.now(UTC)
    p = TradeProposal(
        strategy_id="test_strat",
        symbol="BTCUSDT",
        timeframe="1h",
        direction=OrderSide.SELL,
        entry_price=Decimal("60000.00"),
        stop_loss=Decimal("61000.00"),  # Risk = 1000
        take_profit=Decimal("58000.00"),  # Reward = 2000
        timestamp=now,
        reason="Test breakdown",
    )
    assert p.risk_amount_per_unit == Decimal("1000.00")
    assert p.reward_amount_per_unit == Decimal("2000.00")
    assert p.risk_reward_ratio == Decimal("2.0")


def test_long_proposal_geometry_invariants() -> None:
    """Verifies that invalid Long price relationships (Stop >= Entry or Target <= Entry) raise."""
    now = datetime.now(UTC)

    # Stop Loss >= Entry Price
    with pytest.raises(DomainValidationError, match="Invalid Long proposal: stop_loss"):
        TradeProposal(
            strategy_id="s1",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100"),
            stop_loss=Decimal("105"),
            take_profit=Decimal("120"),
            timestamp=now,
            reason="invalid stop",
        )

    # Take Profit <= Entry Price
    with pytest.raises(DomainValidationError, match="Invalid Long proposal: take_profit"):
        TradeProposal(
            strategy_id="s1",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("95"),
            timestamp=now,
            reason="invalid target",
        )


def test_short_proposal_geometry_invariants() -> None:
    """Verifies that invalid Short price relationships (Stop <= Entry or Target >= Entry) raise."""
    now = datetime.now(UTC)

    # Stop Loss <= Entry Price
    with pytest.raises(DomainValidationError, match="Invalid Short proposal: stop_loss"):
        TradeProposal(
            strategy_id="s1",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.SELL,
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("80"),
            timestamp=now,
            reason="invalid stop",
        )

    # Take Profit >= Entry Price
    with pytest.raises(DomainValidationError, match="Invalid Short proposal: take_profit"):
        TradeProposal(
            strategy_id="s1",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.SELL,
            entry_price=Decimal("100"),
            stop_loss=Decimal("110"),
            take_profit=Decimal("105"),
            timestamp=now,
            reason="invalid target",
        )


def test_minimum_risk_reward_ratio_enforcement() -> None:
    """Verifies that any proposal with RRR below 2.0 is rejected."""
    now = datetime.now(UTC)

    # Entry = 100, Stop = 90 (Risk = 10), Target = 115 (Reward = 15). RRR = 1.5 < 2.0
    with pytest.raises(DomainValidationError, match="minimum Risk-to-Reward ratio"):
        TradeProposal(
            strategy_id="s1",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("115"),
            timestamp=now,
            reason="poor R:R",
        )


def test_proposal_non_utc_timestamp_rejection() -> None:
    """Verifies naive timestamps are rejected."""
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        TradeProposal(
            strategy_id="s1",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("120"),
            timestamp=datetime(2026, 1, 1),
            reason="naive time",
        )
