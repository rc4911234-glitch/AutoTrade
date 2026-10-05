"""Unit tests for Volume Profile and Cumulative Volume Delta (CVD) orderflow engines."""

from datetime import datetime, timezone
from decimal import Decimal
import numpy as np
import pytest

from trad_auto.core.models.market_data import Bar
from trad_auto.quant.cvd_engine import (
    CumulativeVolumeDeltaEngine,
    CVDAnalysisResult,
)
from trad_auto.quant.volume_profile import (
    VolumeProfileEngine,
    VolumeProfileResult,
)
from trad_auto.strategies.concrete.smart_money_scalper import SmartMoneyScalperStrategy


def test_volume_profile_poc_and_value_area():
    """Verify Volume Profile finds the POC price peak and 70% Value Area."""
    # Create 30 candles concentrated around $65,000
    candles = []
    base_time = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    for i in range(30):
        # Heavy volume around $65,000, lower volume elsewhere
        if 10 <= i <= 20:
            p = 65000.0 + (i - 15) * 10
            vol = 500.0  # Big volume
        elif i < 10:
            p = 64000.0 + i * 50
            vol = 50.0  # Thin volume
        else:
            p = 65500.0 + (i - 20) * 50
            vol = 50.0

        candles.append({
            "open": p - 5,
            "high": p + 20,
            "low": p - 20,
            "close": p + 5,
            "volume": vol,
        })

    engine = VolumeProfileEngine(n_bins=40, value_area_pct=0.70)
    res = engine.compute_profile(candles, symbol="BTCUSDT")

    assert isinstance(res, VolumeProfileResult)
    assert res.symbol == "BTCUSDT"
    assert res.total_volume > 0
    # POC must be in the high-volume neighborhood ($64,900 - $65,200)
    assert 64800.0 <= res.poc_price <= 65200.0
    # Value area must enclose POC: VAL <= POC <= VAH
    assert res.val_price <= res.poc_price <= res.vah_price
    assert res.vah_price > res.val_price
    assert res.value_area_relation in ("ABOVE_VAH", "INSIDE_VALUE", "BELOW_VAL")


def test_volume_profile_empty_and_flat_candles():
    """Verify graceful handling of empty or flat ranges."""
    engine = VolumeProfileEngine()
    empty_res = engine.compute_profile([], symbol="ETHUSDT")
    assert empty_res.poc_price == 0.0

    flat_candle = [{"high": 100.0, "low": 100.0, "close": 100.0, "volume": 10.0}]
    flat_res = engine.compute_profile(flat_candle, symbol="SOLUSDT")
    assert flat_res.poc_price == 100.0


def test_cvd_divergence_bullish_absorption():
    """Verify CVD detects bullish absorption when price drops but CVD rises."""
    candles = []
    # Price dropping from 65,500 to 64,500, but aggressive taker buys (>75%)
    for i in range(25):
        price = 65500.0 - (i * 40.0)  # Declining price
        vol = 100.0
        taker_buy = 80.0  # High taker buy => positive delta
        candles.append({
            "close": price,
            "volume": vol,
            "taker_buy_volume": taker_buy,
        })

    cvd_engine = CumulativeVolumeDeltaEngine(lookback_window=20, divergence_threshold_pct=0.15)
    res = cvd_engine.compute_cvd(candles, symbol="BTCUSDT")

    assert isinstance(res, CVDAnalysisResult)
    assert res.divergence_signal == "BULLISH_ABSORPTION"
    assert res.allow_long is True
    assert res.allow_short is False
    assert res.delta_ratio > 0.70


def test_cvd_divergence_bearish_exhaustion():
    """Verify CVD detects bearish exhaustion (trap) when price pumps on seller dominance."""
    candles = []
    # Price rising from 64,000 to 65,500, but aggressive taker sells (taker buy only 20%)
    for i in range(25):
        price = 64000.0 + (i * 60.0)  # Rising price
        vol = 100.0
        taker_buy = 20.0  # Weak taker buy => negative delta
        candles.append({
            "close": price,
            "volume": vol,
            "taker_buy_volume": taker_buy,
        })

    cvd_engine = CumulativeVolumeDeltaEngine(lookback_window=20, divergence_threshold_pct=0.15)
    res = cvd_engine.compute_cvd(candles, symbol="BTCUSDT")

    assert isinstance(res, CVDAnalysisResult)
    assert res.divergence_signal == "BEARISH_EXHAUSTION"
    assert res.allow_long is False
    assert res.allow_short is True
    assert res.delta_ratio < 0.30


def test_scalper_strategy_has_orderflow_integration():
    """Verify SmartMoneyScalperStrategy initializes with Volume Profile & CVD."""
    strategy = SmartMoneyScalperStrategy(
        strategy_id="test_scalper",
        symbols=["BTCUSDT"],
        enable_orderflow=True,
    )
    assert strategy.enable_orderflow is True
    assert strategy.volume_profile_engine is not None
    assert strategy.cvd_engine is not None
    assert hasattr(strategy, "_last_volume_profile")
    assert hasattr(strategy, "_last_cvd_analysis")
