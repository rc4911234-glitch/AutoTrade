"""Unit tests for GMM/Hidden Markov Market Regime Detector."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from trad_auto.core.enums import MarketRegimeType
from trad_auto.core.models.market_data import Bar
from trad_auto.market_data.regime import MarketRegimeDetector


def _generate_synthetic_bars(count: int, trend_drift: float, vol: float) -> list[Bar]:
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    price = 50000.0
    bars: list[Bar] = []
    np.random.seed(42)

    for i in range(count):
        change = price * (trend_drift + np.random.normal(0, vol))
        close = max(100.0, price + change)
        high = max(price, close) + abs(np.random.normal(0, vol * price))
        low = min(price, close) - abs(np.random.normal(0, vol * price))
        bars.append(
            Bar(
                symbol="BTCUSDT",
                timeframe="1m",
                open=Decimal(str(round(price, 2))),
                high=Decimal(str(round(high, 2))),
                low=Decimal(str(round(low, 2))),
                close=Decimal(str(round(close, 2))),
                volume=Decimal("50.0"),
                timestamp=base_time + timedelta(minutes=i),
            )
        )
        price = close
    return bars


def test_regime_detector_insufficient_bars():
    detector = MarketRegimeDetector(min_bars_required=30)
    bars = _generate_synthetic_bars(15, 0.0, 0.001)
    res = detector.classify(bars)
    assert res.regime == MarketRegimeType.UNKNOWN
    assert res.recommendation == "WARMING_UP"


def test_regime_detector_sideways_chop():
    detector = MarketRegimeDetector(min_bars_required=30)
    # Low drift, low vol -> Sideways Chop
    bars = _generate_synthetic_bars(60, 0.00001, 0.0005)
    res = detector.classify(bars)
    assert res.regime == MarketRegimeType.CHOP_SIDEWAYS
    assert res.recommendation == "SUPPRESS_TRENDS"


def test_regime_detector_bullish_trend():
    detector = MarketRegimeDetector(min_bars_required=30)
    # Strong positive drift -> Bull Trend
    bars = _generate_synthetic_bars(60, 0.002, 0.0005)
    res = detector.classify(bars)
    assert res.regime == MarketRegimeType.BULL_TREND
    assert res.recommendation == "PERMIT_LONGS"


def test_regime_detector_high_volatility_chaos():
    detector = MarketRegimeDetector(min_bars_required=30)
    bars = _generate_synthetic_bars(50, 0.0, 0.0005)
    # Add a massive volatility flash-wick bar
    last = bars[-1]
    huge_wick_bar = Bar(
        symbol="BTCUSDT",
        timeframe="1m",
        open=last.close,
        high=last.close * Decimal("1.25"),  # 25% wick
        low=last.close * Decimal("0.75"),
        close=last.close,
        volume=Decimal("5000.0"),
        timestamp=last.timestamp + timedelta(minutes=1),
    )
    bars.append(huge_wick_bar)
    res = detector.classify(bars)
    assert res.regime == MarketRegimeType.HIGH_VOLATILITY_CHAOS
    assert res.recommendation == "FREEZE_CHAOS"
