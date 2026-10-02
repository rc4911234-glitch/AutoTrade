"""Unit tests verifying mathematical correctness of Kelly Criterion dynamic position sizer."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.risk.sizer import VolatilityPositionSizer


@pytest.fixture
def btc_instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=Decimal("0.10"),
        lot_size=Decimal("0.001"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )


@pytest.fixture
def sample_proposal() -> TradeProposal:
    return TradeProposal(
        strategy_id="test_strat",
        symbol="BTCUSDT",
        timeframe="1m",
        direction=OrderSide.BUY,
        entry_price=Decimal("50000.00"),
        stop_loss=Decimal("49000.00"),  # Risk = 1000
        take_profit=Decimal("52000.00"),  # Reward = 2000 (1:2 R:R)
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        confidence=Decimal("0.65"),  # 65% win probability
        reason="Test breakout",
    )


def test_calculate_kelly_fraction_positive_edge():
    # 65% win prob, 2.0 payoff
    # f* = (0.65 * 3 - 1) / 2 = 0.95 / 2 = 0.475
    # Half-Kelly = 0.2375
    # dynamic_risk = 0.01 * (1 + 0.2375) = 0.012375 (1.2375%)
    risk_pct = VolatilityPositionSizer.calculate_kelly_risk_pct(
        win_probability=Decimal("0.65"),
        risk_reward_ratio=Decimal("2.0"),
        base_risk_pct=Decimal("0.01"),
    )
    assert risk_pct > Decimal("0.01")
    assert risk_pct <= Decimal("0.025")


def test_calculate_kelly_fraction_negative_edge():
    # 30% win prob with 2.0 payoff is negative expectancy:
    # 0.30 * 3 - 1 = -0.10 <= 0 -> veto!
    risk_pct = VolatilityPositionSizer.calculate_kelly_risk_pct(
        win_probability=Decimal("0.30"),
        risk_reward_ratio=Decimal("2.0"),
    )
    assert risk_pct == ZERO_DECIMAL


def test_kelly_dynamic_quantity_sizing(btc_instrument: Instrument, sample_proposal: TradeProposal):
    capital = Decimal("10000.00")
    # Base risk = 1% ($100), but with 65% win rate Half-Kelly boosts risk budget
    qty_with_kelly = VolatilityPositionSizer.calculate_quantity(
        proposal=sample_proposal,
        instrument=btc_instrument,
        available_capital=capital,
        risk_pct_per_trade=Decimal("0.01"),
    )

    # Base proposal with no confidence (1% static risk)
    proposal_no_conf = TradeProposal(
        strategy_id="test_strat",
        symbol="BTCUSDT",
        timeframe="1m",
        direction=OrderSide.BUY,
        entry_price=Decimal("50000.00"),
        stop_loss=Decimal("49000.00"),
        take_profit=Decimal("52000.00"),
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        confidence=None,
        reason="Static test",
    )
    qty_static = VolatilityPositionSizer.calculate_quantity(
        proposal=proposal_no_conf,
        instrument=btc_instrument,
        available_capital=capital,
        risk_pct_per_trade=Decimal("0.01"),
    )

    # Kelly with high probability (65%) should size up responsibly
    assert qty_with_kelly > qty_static
    assert qty_with_kelly <= capital / sample_proposal.entry_price
