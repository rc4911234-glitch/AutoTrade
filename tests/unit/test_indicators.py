"""Unit tests for streaming technical indicators (SMA, EMA, ATR, RSI, Bollinger Bands)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar
from trad_auto.indicators.atr import ATR
from trad_auto.indicators.bollinger import BollingerBands
from trad_auto.indicators.moving_averages import EMA, SMA
from trad_auto.indicators.rsi import RSI


def test_sma_calculation_and_lifecycle() -> None:
    """Verifies incremental SMA calculation, window rolling, and reset."""
    sma = SMA(period=3)
    assert not sma.is_ready
    assert sma.value is None

    # Update 1: [10]
    sma.update(Decimal("10"))
    assert not sma.is_ready
    assert sma.value is None

    # Update 2: [10, 20]
    sma.update(Decimal("20"))
    assert not sma.is_ready

    # Update 3: [10, 20, 30] -> Ready! Avg = 20
    sma.update(Decimal("30"))
    assert sma.is_ready
    assert sma.value == Decimal("20")

    # Update 4: [20, 30, 40] -> Avg = 30
    sma.update(Decimal("40"))
    assert sma.value == Decimal("30")

    # Reset
    sma.reset()
    assert not sma.is_ready
    assert sma.value is None

    with pytest.raises(DomainValidationError, match="period must be strictly positive"):
        SMA(period=0)


def test_ema_calculation_and_seed() -> None:
    """Verifies EMA initialization via SMA seed and subsequent recursive updates."""
    ema = EMA(period=3)
    # Multiplier alpha = 2 / (3 + 1) = 0.5
    assert not ema.is_ready

    ema.update(Decimal("10"))
    ema.update(Decimal("20"))
    ema.update(Decimal("30"))  # SMA seed = 20.0
    assert ema.is_ready
    assert ema.value == Decimal("20")

    # Next update: price = 40
    # EMA = (40 * 0.5) + (20 * 0.5) = 20 + 10 = 30
    ema.update(Decimal("40"))
    assert ema.value == Decimal("30")

    # Next update: price = 50
    # EMA = (50 * 0.5) + (30 * 0.5) = 25 + 15 = 40
    ema.update(Decimal("50"))
    assert ema.value == Decimal("40")


def test_atr_true_range_and_wilders_smoothing() -> None:
    """Verifies ATR true range calculation with gap and Wilder's RMA smoothing."""
    atr = ATR(period=3)
    assert not atr.is_ready

    # Bar 1: H=105, L=95, C=100 -> TR = 10
    atr.update_hlc(Decimal("105"), Decimal("95"), Decimal("100"))
    assert not atr.is_ready

    # Bar 2: H=115, L=102, C=110 -> PrevC=100. TR = max(115-102=13, |115-100|=15, |102-100|=2) = 15
    atr.update_hlc(Decimal("115"), Decimal("102"), Decimal("110"))
    assert not atr.is_ready

    # Bar 3: H=112, L=105, C=108 -> PrevC=110. TR = max(7, 2, 5) = 7
    # Initial SMA of TRs = (10 + 15 + 7) / 3 = 32 / 3 = 10.666...
    atr.update_hlc(Decimal("112"), Decimal("105"), Decimal("108"))
    assert atr.is_ready
    expected_seed = Decimal("32") / Decimal("3")
    assert atr.value == expected_seed

    # Bar 4: H=120, L=106, C=118 -> PrevC=108. TR = max(14, 12, 2) = 14
    # Wilder's update: (previous * 2 + 14) / 3
    atr.update_hlc(Decimal("120"), Decimal("106"), Decimal("118"))
    expected_next = (expected_seed * Decimal("2") + Decimal("14")) / Decimal("3")
    assert atr.value == expected_next

    # update_bar with Bar instance
    bar = Bar(
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="1m",
        open=Decimal("118"),
        high=Decimal("125"),
        low=Decimal("115"),
        close=Decimal("122"),
        volume=Decimal("10"),
    )
    val = atr.update_bar(bar)
    assert val is not None


def test_rsi_bounds_and_oscillations() -> None:
    """Verifies that RSI is bounded in [0, 100] and properly reflects streaks."""
    rsi = RSI(period=3)

    # 1. Monotonically increasing prices -> RSI should reach 100
    prices_up = [Decimal(f"{100 + i * 5}") for i in range(5)]
    for p in prices_up:
        rsi.update(p)

    assert rsi.is_ready
    assert rsi.value == Decimal("100.0")

    # 2. Reset and test monotonically decreasing prices -> RSI should reach 0
    rsi.reset()
    prices_down = [Decimal(f"{100 - i * 5}") for i in range(5)]
    for p in prices_down:
        rsi.update(p)

    assert rsi.is_ready
    assert rsi.value == Decimal("0.0")

    # 3. Oscillating prices
    rsi.reset()
    prices_osc = [
        Decimal("100"),
        Decimal("105"),
        Decimal("102"),
        Decimal("106"),
        Decimal("103"),
        Decimal("107"),
    ]
    for p in prices_osc:
        rsi.update(p)

    assert rsi.is_ready
    assert rsi.value is not None
    assert Decimal("0.0") <= rsi.value <= Decimal("100.0")


def test_bollinger_bands_calculation() -> None:
    """Verifies Bollinger middle band, upper band, lower band, and bandwidth."""
    bb = BollingerBands(period=3, num_std=Decimal("2.0"))
    assert not bb.is_ready
    assert bb.middle is None

    # Feed values: 10, 20, 30
    bb.update(Decimal("10"))
    bb.update(Decimal("20"))
    bb.update(Decimal("30"))

    assert bb.is_ready
    assert bb.middle == Decimal("20")
    assert bb.std_dev is not None
    # Variance of [10, 20, 30] with mean 20: ((10-20)^2 + 0 + (30-20)^2) / 3 = 200 / 3 = 66.666...
    # std_dev = sqrt(66.666...) ~= 8.1649658
    assert bb.upper is not None
    assert bb.lower is not None
    assert bb.upper > bb.middle
    assert bb.lower < bb.middle
    assert bb.bandwidth is not None
    assert bb.bandwidth > Decimal("0")
