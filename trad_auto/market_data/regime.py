"""Market Regime Detector using Gaussian Mixture Modeling (GMM) / Hidden Markov state clustering."""

from dataclasses import dataclass
from decimal import Decimal
import logging
import math
from typing import Sequence

import numpy as np
from sklearn.mixture import GaussianMixture

from trad_auto.core.enums import MarketRegimeType
from trad_auto.core.models.market_data import Bar

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegimeClassification:
    """Quantitative regime classification snapshot."""

    regime: MarketRegimeType
    probability: float
    volatility_zscore: float
    mean_drift_bps: float  # Basis points per bar
    recommendation: str  # PERMIT_LONGS, PERMIT_SHORTS, SUPPRESS_TRENDS, FREEZE_CHAOS


class MarketRegimeDetector:
    """Identifies latent market regimes (Bull Trend, Bear Trend, Chop, Chaos) using Gaussian Mixtures.

    Mathematical Principles:
    1. Returns Drift: Measures directional momentum drift across rolling bars.
    2. Parkinson Volatility: High-Low log volatility metric with higher statistical efficiency
       than simple close-to-close variance.
    3. Variance Decomposition: Distinguishes between directed momentum expansion and non-directional
       erratic whipsaws (liquidity hunts).
    """

    def __init__(self, min_bars_required: int = 30) -> None:
        self.min_bars_required = min_bars_required

    def classify(self, bars: Sequence[Bar]) -> RegimeClassification:
        """Classifies the current market regime based on a sequence of historical bars."""
        if len(bars) < self.min_bars_required:
            return RegimeClassification(
                regime=MarketRegimeType.UNKNOWN,
                probability=0.5,
                volatility_zscore=0.0,
                mean_drift_bps=0.0,
                recommendation="WARMING_UP",
            )

        closes = np.array([float(b.close) for b in bars], dtype=np.float64)
        highs = np.array([float(b.high) for b in bars], dtype=np.float64)
        lows = np.array([float(b.low) for b in bars], dtype=np.float64)

        # 1. Log Returns
        returns = np.diff(np.log(closes))

        # 2. Parkinson Volatility: ((ln(H/L))^2) / (4 * ln(2))
        with np.errstate(divide="ignore", invalid="ignore"):
            hl_ratio = np.maximum(highs[1:] / np.maximum(lows[1:], 1e-8), 1.0)
            parkinson_vol = np.sqrt((np.log(hl_ratio) ** 2) / (4.0 * np.log(2.0)))

        # 3. Features matrix: [Log Returns, Parkinson Volatility]
        X = np.column_stack([returns, parkinson_vol])

        # Current bar metrics
        curr_ret = float(returns[-1])
        curr_vol = float(parkinson_vol[-1])
        median_vol = float(np.median(parkinson_vol))
        std_vol = float(np.std(parkinson_vol)) or 1e-6
        vol_zscore = (curr_vol - median_vol) / std_vol

        # 4. Check for Extreme Volatility Chaos (Z-Score > 3.0)
        if vol_zscore > 3.0:
            return RegimeClassification(
                regime=MarketRegimeType.HIGH_VOLATILITY_CHAOS,
                probability=0.95,
                volatility_zscore=round(vol_zscore, 2),
                mean_drift_bps=round(curr_ret * 10000, 1),
                recommendation="FREEZE_CHAOS",
            )

        # 5. Fit 3-Component Gaussian Mixture (Bull, Bear, Sideways)
        try:
            gmm = GaussianMixture(n_components=3, covariance_type="diag", random_state=42, max_iter=50)
            gmm.fit(X)
            probs = gmm.predict_proba(X[-1:])[0]
            pred_cluster = int(np.argmax(probs))
            conf = float(probs[pred_cluster])

            means = gmm.means_[:, 0]  # mean return of each cluster
            cluster_mean_bps = float(means[pred_cluster]) * 10000
            recent_drift = float(np.mean(returns[-5:])) * 10000  # in basis points

            if cluster_mean_bps > 1.5 and recent_drift > 1.0:
                regime = MarketRegimeType.BULL_TREND
                recommendation = "PERMIT_LONGS"
            elif cluster_mean_bps < -1.5 and recent_drift < -1.0:
                regime = MarketRegimeType.BEAR_TREND
                recommendation = "PERMIT_SHORTS"
            else:
                regime = MarketRegimeType.CHOP_SIDEWAYS
                recommendation = "SUPPRESS_TRENDS"

            return RegimeClassification(
                regime=regime,
                probability=round(conf, 3),
                volatility_zscore=round(vol_zscore, 2),
                mean_drift_bps=round(recent_drift, 1),
                recommendation=recommendation,
            )

        except Exception as exc:
            logger.debug("[RegimeDetector] GMM fit fallback: %s", exc)
            # Analytical Rule-Based Fallback
            recent_drift = float(np.mean(returns[-5:])) * 10000
            if recent_drift > 5.0 and vol_zscore < 2.0:
                return RegimeClassification(
                    regime=MarketRegimeType.BULL_TREND,
                    probability=0.75,
                    volatility_zscore=round(vol_zscore, 2),
                    mean_drift_bps=round(recent_drift, 1),
                    recommendation="PERMIT_LONGS",
                )
            elif recent_drift < -5.0 and vol_zscore < 2.0:
                return RegimeClassification(
                    regime=MarketRegimeType.BEAR_TREND,
                    probability=0.75,
                    volatility_zscore=round(vol_zscore, 2),
                    mean_drift_bps=round(recent_drift, 1),
                    recommendation="PERMIT_SHORTS",
                )
            else:
                return RegimeClassification(
                    regime=MarketRegimeType.CHOP_SIDEWAYS,
                    probability=0.70,
                    volatility_zscore=round(vol_zscore, 2),
                    mean_drift_bps=round(recent_drift, 1),
                    recommendation="SUPPRESS_TRENDS",
                )
