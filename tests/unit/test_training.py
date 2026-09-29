"""Unit tests for Trad-Auto Institutional Quant AI Training Pipeline."""

import numpy as np
import pytest

from trad_auto.training.data_downloader import BinanceDataDownloader
from trad_auto.training.features import QuantFeatureExtractor
from trad_auto.training.labeling import TripleBarrierLabeler
from trad_auto.training.trainer import QuantModelTrainer
from trad_auto.training.walk_forward import WalkForwardEvaluator


@pytest.fixture()
def sample_candles() -> list[dict[str, object]]:
    """Generates 300 realistic candles for deterministic testing."""
    downloader = BinanceDataDownloader()
    return downloader.generate_synthetic_klines(symbol="BTCUSDT", count=300, seed=123)


class TestBinanceDataDownloader:
    """Tests for market data acquisition and synthetic generator."""

    def test_synthetic_kline_generation(self) -> None:
        downloader = BinanceDataDownloader()
        candles = downloader.generate_synthetic_klines(symbol="BTCUSDT", count=250)
        assert len(candles) == 250

        first = candles[0]
        assert "open" in first
        assert "high" in first
        assert "low" in first
        assert "close" in first
        assert "volume" in first
        assert "taker_buy_volume" in first
        assert first["high"] >= first["low"]
        assert first["high"] >= first["open"]
        assert first["high"] >= first["close"]

    def test_fetch_klines_fallback_on_network_error(self) -> None:
        downloader = BinanceDataDownloader(timeout_seconds=0.001)
        # Mock bad URL or timeout to trigger fallback
        downloader.BASE_URL = "http://127.0.0.1:1"
        candles = downloader.fetch_klines(limit=100)
        assert len(candles) == 100
        assert candles[0]["open"] > 0


class TestQuantFeatureExtractor:
    """Tests for tabular alpha feature calculation."""

    def test_feature_extraction(self, sample_candles: list[dict[str, object]]) -> None:
        extractor = QuantFeatureExtractor()
        X, names, indices = extractor.extract_features(sample_candles)

        assert len(names) == 22
        assert X.shape[1] == 22
        assert len(X) == len(indices)
        assert len(indices) == len(sample_candles) - 50  # 50 warmup rows removed

        # Verify no NaNs or Infinities
        assert not np.isnan(X).any()
        assert not np.isinf(X).any()

    def test_insufficient_candles_raises_value_error(self) -> None:
        extractor = QuantFeatureExtractor()
        downloader = BinanceDataDownloader()
        short_candles = downloader.generate_synthetic_klines(count=40)
        with pytest.raises(ValueError, match="at least 60 candles"):
            extractor.extract_features(short_candles)


class TestTripleBarrierLabeler:
    """Tests for Triple Barrier Method labeling."""

    def test_triple_barrier_labeling_long(
        self, sample_candles: list[dict[str, object]]
    ) -> None:
        extractor = QuantFeatureExtractor()
        _, _, indices = extractor.extract_features(sample_candles)

        labeler = TripleBarrierLabeler(
            take_profit_ratio=0.01,
            stop_loss_ratio=0.005,
            max_horizon_bars=10,
        )
        labels, returns = labeler.label_candles(sample_candles, indices, direction="LONG")

        assert len(labels) == len(indices)
        assert len(returns) == len(indices)
        # Labels must be strictly 0 or 1
        assert set(np.unique(labels)).issubset({0, 1})

    def test_triple_barrier_labeling_short(
        self, sample_candles: list[dict[str, object]]
    ) -> None:
        extractor = QuantFeatureExtractor()
        _, _, indices = extractor.extract_features(sample_candles)

        labeler = TripleBarrierLabeler()
        labels, returns = labeler.label_candles(sample_candles, indices, direction="SHORT")
        assert len(labels) == len(indices)
        assert set(np.unique(labels)).issubset({0, 1})


class TestWalkForwardEvaluator:
    """Tests for out-of-sample walk-forward cross validation."""

    def test_walk_forward_evaluation(self, sample_candles: list[dict[str, object]]) -> None:
        extractor = QuantFeatureExtractor()
        X, _, indices = extractor.extract_features(sample_candles)

        labeler = TripleBarrierLabeler()
        y, returns = labeler.label_candles(sample_candles, indices)

        evaluator = WalkForwardEvaluator(decision_threshold=0.50)
        perf = evaluator.evaluate(X, y, returns)

        assert perf.total_trades >= 0
        assert 0.0 <= perf.win_rate_pct <= 100.0
        assert perf.profit_factor >= 0.0


class TestQuantModelTrainer:
    """Tests for full training pipeline and artifact serialization."""

    def test_train_and_load_pipeline(
        self, tmp_path: pytest.TempPathFactory, sample_candles: list[dict[str, object]]
    ) -> None:
        trainer = QuantModelTrainer(model_dir=str(tmp_path), decision_threshold=0.55)

        result = trainer.train_pipeline(candles=sample_candles, model_name="test_model")
        assert result.sample_count > 0
        assert len(result.feature_names) == 22
        assert result.model_path.endswith("test_model.joblib")
        assert result.metadata_path.endswith("test_model_metadata.json")

        # Load back model
        loaded = trainer.load_model(model_name="test_model")
        assert loaded is not None
        model, metadata = loaded
        assert metadata["model_name"] == "test_model"
        assert len(metadata["feature_names"]) == 22

        # Verify model predict_proba
        extractor = QuantFeatureExtractor()
        X, _, _ = extractor.extract_features(sample_candles)
        probs = model.predict_proba(X[:5])
        assert probs.shape == (5, 2)
        assert np.all(probs >= 0.0) and np.all(probs <= 1.0)
