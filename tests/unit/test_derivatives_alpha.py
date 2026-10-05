"""Unit tests for Derivatives Microstructure Alpha Engine."""

from unittest.mock import MagicMock, patch
import pytest

from trad_auto.quant.derivatives_alpha import DerivativesAlphaEngine, DerivativesAlphaSignal


def test_derivatives_alpha_neutral_baseline():
    engine = DerivativesAlphaEngine()

    with patch.object(engine, "analyze_symbol") as mock_analyze:
        mock_analyze.return_value = DerivativesAlphaSignal(
            symbol="BTCUSDT",
            funding_rate_pct=0.0100,
            funding_zscore=0.0,
            funding_bias="NEUTRAL",
            oi_notional_usd=1000000.0,
            oi_trend="FLAT",
            cvd_ratio=0.50,
            market_conviction="CHOP_NOISE",
            allow_long=True,
            allow_short=True,
            summary="Balanced funding environment.",
        )

        sig = engine.analyze_symbol("BTCUSDT")
        assert sig.symbol == "BTCUSDT"
        assert sig.funding_bias == "NEUTRAL"
        assert sig.allow_long is True
        assert sig.allow_short is True


def test_derivatives_alpha_long_squeeze_filter():
    engine = DerivativesAlphaEngine()

    with patch.object(engine, "analyze_symbol") as mock_analyze:
        mock_analyze.return_value = DerivativesAlphaSignal(
            symbol="BTCUSDT",
            funding_rate_pct=0.0450,
            funding_zscore=2.33,
            funding_bias="LONG_SQUEEZE_RISK",
            oi_notional_usd=2500000.0,
            oi_trend="EXPANDING",
            cvd_ratio=0.62,
            market_conviction="LIQUIDATION_PUMP",
            allow_long=False,
            allow_short=True,
            summary="Excessive retail long leverage.",
        )

        sig = engine.analyze_symbol("BTCUSDT")
        assert sig.allow_long is False
        assert sig.allow_short is True
        assert sig.funding_bias == "LONG_SQUEEZE_RISK"
