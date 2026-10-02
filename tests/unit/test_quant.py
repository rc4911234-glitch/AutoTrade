"""Tests for Alpha158 Factor Library and Cross-Sectional Ranker."""

import numpy as np
import pytest
from trad_auto.quant.alpha158 import Alpha158Engine
from trad_auto.quant.cross_sectional_ranker import (
    CrossSectionalRanker,
    CrossSectionalSnapshot,
    AssetRanking,
)


def _make_candles(n: int = 100, base_price: float = 60000.0, seed: int = 42) -> list[dict]:
    """Generate synthetic OHLCV candles for testing."""
    np.random.seed(seed)
    returns = np.random.randn(n) * 0.005
    prices = base_price * np.cumprod(1 + returns)
    candles = []
    for i in range(n):
        c = float(prices[i])
        h = c * (1 + abs(np.random.randn()) * 0.003)
        l = c * (1 - abs(np.random.randn()) * 0.003)
        o = c * (1 + np.random.randn() * 0.002)
        v = float(np.random.uniform(100, 1000))
        candles.append({
            "open": o, "high": h, "low": l, "close": c,
            "volume": v, "taker_buy_volume": v * 0.5,
        })
    return candles


# ─── Alpha158Engine Tests ─────────────────────────────────────────

class TestAlpha158Engine:
    def test_feature_count_is_158(self):
        """Must produce exactly 158 feature names."""
        engine = Alpha158Engine()
        assert len(engine.FEATURE_NAMES) == 158

    def test_extract_features_shape(self):
        """Output X must be (n_valid, 158) with valid_indices."""
        engine = Alpha158Engine()
        candles = _make_candles(100)
        X, names, valid_idx = engine.extract_features(candles)
        
        assert X.shape[1] == 158
        assert X.shape[0] == len(valid_idx)
        assert X.shape[0] == 100 - 60  # warmup = 60
        assert len(names) == 158

    def test_no_nan_or_inf(self):
        """Output must be sanitized — no NaN or Inf."""
        engine = Alpha158Engine()
        candles = _make_candles(150)
        X, _, _ = engine.extract_features(candles)
        
        assert not np.any(np.isnan(X)), "Found NaN in features"
        assert not np.any(np.isinf(X)), "Found Inf in features"

    def test_too_few_candles_raises(self):
        """Must raise ValueError with fewer than 61 candles."""
        engine = Alpha158Engine()
        candles = _make_candles(50)
        with pytest.raises(ValueError, match="warmup"):
            engine.extract_features(candles)

    def test_kbar_features_present(self):
        """KBar features KMID through KSFT2 must be in output."""
        engine = Alpha158Engine()
        kbar_names = ['KMID', 'KLEN', 'KMID2', 'KUP', 'KUP2', 'KLOW', 'KLOW2', 'KSFT', 'KSFT2']
        for name in kbar_names:
            assert name in engine.FEATURE_NAMES, f"Missing KBar feature: {name}"

    def test_rolling_features_all_windows(self):
        """Rolling features must exist for all 5 windows."""
        engine = Alpha158Engine()
        for w in [5, 10, 20, 30, 60]:
            for base in ['ROC', 'MA', 'STD', 'CORR', 'RSV', 'VMA']:
                fname = f"{base}_{w}"
                assert fname in engine.FEATURE_NAMES, f"Missing rolling feature: {fname}"

    def test_feature_names_match_columns(self):
        """Feature names list length must match X columns."""
        engine = Alpha158Engine()
        candles = _make_candles(100)
        X, names, _ = engine.extract_features(candles)
        assert len(names) == X.shape[1]

    def test_zero_future_leak(self):
        """Features at row i must not change if we add data after i."""
        engine = Alpha158Engine()
        candles_100 = _make_candles(100)
        candles_90 = candles_100[:90]
        
        X_90, _, idx_90 = engine.extract_features(candles_90)
        X_100, _, idx_100 = engine.extract_features(candles_100)
        
        # Row at candle index 80 (idx_90: 80-60=20, idx_100: 80-60=20)
        # Both should be identical since candle 80 only uses data up to 80
        np.testing.assert_array_almost_equal(
            X_90[20], X_100[20], decimal=10,
            err_msg="Future data leak detected!"
        )

    def test_multi_asset_extraction(self):
        """Multi-asset extraction must return features per symbol."""
        engine = Alpha158Engine()
        assets = {
            "BTCUSDT": _make_candles(100, 60000, seed=1),
            "ETHUSDT": _make_candles(100, 3000, seed=2),
        }
        result = engine.extract_features_multi_asset(assets)
        
        assert "BTCUSDT" in result
        assert "ETHUSDT" in result
        assert result["BTCUSDT"][0].shape[1] == 158
        assert result["ETHUSDT"][0].shape[1] == 158


# ─── CrossSectionalRanker Tests ──────────────────────────────────

class TestCrossSectionalRanker:
    def _get_asset_features(self) -> tuple[dict[str, np.ndarray], list[str]]:
        """Helper to generate multi-asset features."""
        engine = Alpha158Engine()
        assets = {
            "BTCUSDT": _make_candles(100, 60000, seed=10),
            "ETHUSDT": _make_candles(100, 3000, seed=20),
            "SOLUSDT": _make_candles(100, 150, seed=30),
            "BNBUSDT": _make_candles(100, 400, seed=40),
        }
        multi = engine.extract_features_multi_asset(assets)
        latest = {sym: X[-1:] for sym, (X, _, _) in multi.items()}
        return latest, engine.FEATURE_NAMES

    def test_ranking_order(self):
        """Rankings must be sorted 1 to N."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        snapshot = ranker.rank_assets(latest, names)
        
        ranks = [r.rank for r in snapshot.rankings]
        assert ranks == [1, 2, 3, 4]

    def test_top_asset_is_rank_1(self):
        """Top asset must match the rank-1 symbol."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        snapshot = ranker.rank_assets(latest, names)
        
        assert snapshot.top_asset == snapshot.rankings[0].symbol

    def test_spread_positive(self):
        """Spread between top and bottom must be >= 0."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        snapshot = ranker.rank_assets(latest, names)
        
        assert snapshot.spread >= 0

    def test_regime_detection(self):
        """Regime must be one of three valid labels."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        snapshot = ranker.rank_assets(latest, names)
        
        assert snapshot.regime_label in ('concentrated', 'dispersed', 'flat')

    def test_should_trade_returns_tuple(self):
        """should_trade must return (bool, str) tuple."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        snapshot = ranker.rank_assets(latest, names)
        should, reason = ranker.should_trade(snapshot)
        
        assert isinstance(should, bool)
        assert isinstance(reason, str)

    def test_historical_performance_after_ranking(self):
        """After ranking, history should be populated."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        ranker.rank_assets(latest, names)
        
        perf = ranker.get_historical_performance()
        assert perf["snapshots_recorded"] == 1

    def test_empty_features_raises(self):
        """Empty features dict must raise ValueError."""
        ranker = CrossSectionalRanker()
        with pytest.raises(ValueError):
            ranker.rank_assets({}, [])

    def test_zscore_normalization(self):
        """Z-scores across assets must sum approximately to 0."""
        ranker = CrossSectionalRanker()
        latest, names = self._get_asset_features()
        snapshot = ranker.rank_assets(latest, names)
        
        z_sum = sum(r.cs_zscore for r in snapshot.rankings)
        assert abs(z_sum) < 0.01, f"Z-scores don't sum to ~0: {z_sum}"
