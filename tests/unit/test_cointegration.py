"""Unit tests for Statistical Arbitrage Cointegration Engine."""

import numpy as np
import pytest

from trad_auto.math.cointegration import CointegrationEngine, StatArbSignal


def test_cointegration_insufficient_bars() -> None:
    engine = CointegrationEngine(min_bars_required=30)
    res = engine.analyze_pair(
        "BTCUSDT",
        [60000.0, 60100.0],
        "ETHUSDT",
        [3000.0, 3010.0],
    )
    assert res is None


def test_cointegration_synthetic_pair_spread() -> None:
    np.random.seed(42)
    n = 100
    # X: random walk around 65000
    x = 65000.0 + np.cumsum(np.random.randn(n) * 100.0)
    # Y: cointegrated with beta=0.05 + mean-reverting stationary noise
    noise = np.random.randn(n) * 5.0
    y = 0.05 * x + 100.0 + noise

    engine = CointegrationEngine(min_bars_required=30)
    res = engine.analyze_pair("BTCUSDT", x, "ETHUSDT", y)

    assert res is not None
    assert res.symbol_x == "BTCUSDT"
    assert res.symbol_y == "ETHUSDT"
    assert pytest.approx(res.hedge_ratio, rel=1e-1) == 0.05
    assert res.correlation > 0.90
    assert res.half_life_bars > 0
    assert abs(res.z_score) < 4.0


def test_cointegration_extreme_positive_divergence() -> None:
    np.random.seed(42)
    n = 60
    x = 60000.0 + np.cumsum(np.random.randn(n) * 20.0)
    y = 0.05 * x + 50.0 + np.random.randn(n) * 2.0
    # Shock y on the last bar so ETH spikes abnormally high relative to BTC
    y[-1] += 50.0

    engine = CointegrationEngine(z_entry_threshold=2.0)
    res = engine.analyze_pair("BTCUSDT", x, "ETHUSDT", y)

    assert res is not None
    assert res.z_score >= 2.0
    assert res.signal == StatArbSignal.SHORT_SPREAD


def test_cointegration_extreme_negative_divergence() -> None:
    np.random.seed(42)
    n = 60
    x = 60000.0 + np.cumsum(np.random.randn(n) * 20.0)
    y = 0.05 * x + 50.0 + np.random.randn(n) * 2.0
    # Shock y on the last bar downwards so ETH drops abnormally relative to BTC
    y[-1] -= 50.0

    engine = CointegrationEngine(z_entry_threshold=2.0)
    res = engine.analyze_pair("BTCUSDT", x, "ETHUSDT", y)

    assert res is not None
    assert res.z_score <= -2.0
    assert res.signal == StatArbSignal.LONG_SPREAD
