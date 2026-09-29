"""Unit tests for Average Directional Index (ADX) indicator."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar
from trad_auto.indicators.adx import ADX


def _make_bar(
    timestamp: datetime,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    open_: Decimal | None = None,
) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        timeframe="1m",
        open=open_ or close,
        high=high,
        low=low,
        close=close,
        volume=Decimal("100.0"),
        timestamp=timestamp,
    )


def test_adx_validation_and_properties() -> None:
    with pytest.raises(DomainValidationError):
        ADX(period=0)

    adx = ADX(period=14)
    assert adx.name == "ADX(14)"
    assert adx.period == 14
    assert adx.is_ready is False
    assert adx.value is None
    assert adx.plus_di is None
    assert adx.minus_di is None

    with pytest.raises(DomainValidationError):
        adx.update_hlc(high=Decimal("49000.0"), low=Decimal("50000.0"), close=Decimal("49500.0"))


def test_adx_strong_uptrend_detection() -> None:
    adx = ADX(period=7)  # Shorter period for quick convergence
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    base = Decimal("50000.0")
    # Feed 30 strongly rising bars (consistent higher highs & higher lows)
    for i in range(30):
        t = now + timedelta(minutes=i)
        step = Decimal(str(i * 50))
        hi = base + step + Decimal("40.0")
        lo = base + step - Decimal("10.0")
        cl = base + step + Decimal("30.0")
        bar = _make_bar(t, high=hi, low=lo, close=cl)
        adx.update_bar(bar)

    assert adx.is_ready is True
    assert adx.value is not None
    assert adx.plus_di is not None
    assert adx.minus_di is not None
    # Uptrend: +DI must dominate -DI
    assert adx.plus_di > adx.minus_di
    # ADX should indicate a very strong trend
    assert adx.value >= Decimal("25.0")
    assert adx.is_trending(threshold=Decimal("25.0")) is True


def test_adx_sideways_chop_detection() -> None:
    adx = ADX(period=7)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed 30 alternating bars within a tight fixed range of $50000 - $50010
    for i in range(30):
        t = now + timedelta(minutes=i)
        if i % 2 == 0:
            hi = Decimal("50010.0")
            lo = Decimal("50000.0")
            cl = Decimal("50008.0")
        else:
            hi = Decimal("50008.0")
            lo = Decimal("49998.0")
            cl = Decimal("50001.0")
        bar = _make_bar(t, high=hi, low=lo, close=cl)
        adx.update_bar(bar)

    assert adx.is_ready is True
    assert adx.value is not None
    # ADX in sideways chop should be low (< 22.0)
    assert adx.value < Decimal("22.0")
    assert adx.is_trending(threshold=Decimal("25.0")) is False


def test_adx_reset_clears_state() -> None:
    adx = ADX(period=5)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    for i in range(15):
        t = now + timedelta(minutes=i)
        bar = _make_bar(
            t,
            high=Decimal(str(50000 + i * 20)),
            low=Decimal(str(49980 + i * 20)),
            close=Decimal(str(49990 + i * 20)),
        )
        adx.update_bar(bar)

    assert adx.is_ready is True
    adx.reset()
    assert adx.is_ready is False
    assert adx.value is None
    assert adx.plus_di is None
    assert adx.minus_di is None
