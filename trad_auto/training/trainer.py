"""Quantitative machine learning trainer coordinating feature extraction and persistence."""

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from trad_auto.training.data_downloader import BinanceDataDownloader
from trad_auto.training.features import QuantFeatureExtractor
from trad_auto.training.labeling import TripleBarrierLabeler
from trad_auto.training.walk_forward import BacktestPerformance, WalkForwardEvaluator

logger = logging.getLogger(__name__)


@dataclass
class TrainingResult:
    """Artifact metadata and performance summary of a trained model."""

    model_path: str
    metadata_path: str
    feature_names: list[str]
    sample_count: int
    decision_threshold: float
    performance: BacktestPerformance


class QuantModelTrainer:
    """Coordinates data downloading, quant feature engineering, and model training."""

    def __init__(
        self,
        model_dir: str = "data/models",
        decision_threshold: float = 0.55,
        feature_extractor: Any = None,
        use_alpha158: bool = True,
    ) -> None:
        self.model_dir = model_dir
        self.decision_threshold = decision_threshold
        if feature_extractor is not None:
            self.feature_extractor = feature_extractor
        elif use_alpha158:
            from trad_auto.quant.alpha158 import Alpha158Engine

            self.feature_extractor = Alpha158Engine()
        else:
            self.feature_extractor = QuantFeatureExtractor()
        self.labeler = TripleBarrierLabeler()
        self.downloader = BinanceDataDownloader()
        self.evaluator = WalkForwardEvaluator(decision_threshold=decision_threshold)

        os.makedirs(self.model_dir, exist_ok=True)

    def train_pipeline(
        self,
        candles: list[dict[str, Any]] | None = None,
        candle_count: int = 2000,
        model_name: str = "btc_scalper_ml",
    ) -> TrainingResult:
        """Executes full quant machine learning training and verification pipeline."""
        # 1. Acquire Data
        if candles is None or len(candles) < 100:
            logger.info("Fetching %d klines from Binance API/Synthetic generator...", candle_count)
            candles = self.downloader.fetch_klines(symbol="BTCUSDT", limit=candle_count)

        # 2. Extract Features
        logger.info("Extracting quantitative alpha features...")
        X, feature_names, valid_indices = self.feature_extractor.extract_features(candles)

        # 3. Apply Triple Barrier Labeling
        logger.info("Applying Triple Barrier Method labeling...")
        y, returns = self.labeler.label_candles(candles, valid_indices, direction="LONG")

        # 4. Walk-Forward Out-Of-Sample Validation
        logger.info("Running Purged Walk-Forward performance evaluation...")
        performance = self.evaluator.evaluate(X, y, returns)
        logger.info(
            "Walk-Forward Results: WinRate=%.1f%%, ProfitFactor=%.2f, "
            "Return=%.2f%%, Sharpe=%.2f, MaxDD=%.2f%%",
            performance.win_rate_pct,
            performance.profit_factor,
            performance.total_net_return_pct,
            performance.sharpe_ratio,
            performance.max_drawdown_pct,
        )

        # 5. Train Production Stacking Ensemble (HGB + Random Forest)
        logger.info("Training production QuantStackingEnsemble (HGB + Random Forest)...")
        from trad_auto.training.ensemble_trainer import QuantStackingEnsemble
        model = QuantStackingEnsemble(random_state=42)
        model.fit(X, y)

        # 6. Serialize Model and Metadata Artifacts
        model_path = os.path.join(self.model_dir, f"{model_name}.joblib")
        metadata_path = os.path.join(self.model_dir, f"{model_name}_metadata.json")

        joblib.dump(model, model_path)

        metadata = {
            "model_name": model_name,
            "created_at": str(np.datetime64("now")),
            "feature_names": feature_names,
            "sample_count": len(X),
            "decision_threshold": self.decision_threshold,
            "performance": asdict(performance),
        }
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        logger.info("Quant Model saved to %s and metadata to %s", model_path, metadata_path)

        return TrainingResult(
            model_path=model_path,
            metadata_path=metadata_path,
            feature_names=feature_names,
            sample_count=len(X),
            decision_threshold=self.decision_threshold,
            performance=performance,
        )

    def load_model(
        self, model_name: str = "btc_scalper_ml"
    ) -> tuple[Any, dict[str, Any]] | None:
        """Loads serialized model and metadata for live inference."""
        model_path = os.path.join(self.model_dir, f"{model_name}.joblib")
        metadata_path = os.path.join(self.model_dir, f"{model_name}_metadata.json")

        if not os.path.exists(model_path) or not os.path.exists(metadata_path):
            return None

        try:
            model = joblib.load(model_path)
            with open(metadata_path, encoding="utf-8") as f:
                metadata: dict[str, Any] = json.load(f)
            return model, metadata
        except Exception as exc:
            logger.warning("Failed to load model from %s: %s", model_path, exc)
            return None
