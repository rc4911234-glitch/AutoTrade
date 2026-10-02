from dataclasses import dataclass, field
from typing import Any, Optional
import numpy as np
from datetime import datetime, timezone
from collections import deque


@dataclass
class AssetRanking:
    """Ranking result for a single asset at a point in time."""
    symbol: str
    rank: int  # 1 = best predicted alpha
    alpha_score: float  # Raw predicted alpha / z-score
    cs_zscore: float  # Cross-sectional z-score (standardized across assets)
    percentile: float  # 0-1 percentile rank
    is_tradeable: bool  # Passes minimum alpha threshold
    features_snapshot: dict[str, float]  # Key features for this asset
    timestamp: str


@dataclass  
class CrossSectionalSnapshot:
    """Complete cross-sectional analysis at one point in time."""
    timestamp: str
    rankings: list[AssetRanking]  # Sorted by rank (1 = best)
    top_asset: str  # Symbol of rank-1 asset
    spread: float  # Alpha spread between rank-1 and rank-last
    dispersion: float  # Std dev of alpha scores (market regime indicator)
    n_tradeable: int  # Number of assets passing threshold
    regime_label: str  # 'concentrated' / 'dispersed' / 'flat'


class CrossSectionalRanker:
    """Cross-Sectional Multi-Asset Alpha Ranking Engine.
    
    Institutional-grade cross-sectional analysis:
    1. Takes Alpha158 features for multiple assets
    2. Computes composite alpha score for each asset  
    3. Standardizes scores cross-sectionally (z-score across assets)
    4. Ranks assets and identifies the best opportunity
    5. Only recommends trading if alpha spread is significant
    """
    
    def __init__(
        self,
        min_alpha_zscore: float = 0.5,  # Minimum z-score to be tradeable
        min_spread: float = 0.3,  # Minimum spread between top and bottom
        lookback_periods: int = 20,  # Rolling normalization window
        alpha_weights: Optional[dict[str, float]] = None,  # Feature importance weights
    ):
        self.min_alpha_zscore = min_alpha_zscore
        self.min_spread = min_spread
        self.lookback_periods = lookback_periods
        
        self.alpha_weights = alpha_weights or {
            'momentum': 0.35,
            'reversion': 0.25,
            'volume': 0.20,
            'volatility': 0.20
        }
        
        # Momentum signals
        self.momentum_factors = ['ROC_5', 'ROC_10', 'ROC_20', 'SUMP_5', 'SUMP_10', 'SUMD_5', 'SUMD_10']
        # Mean-reversion signals
        self.reversion_factors = ['RSV_5', 'RSV_10', 'RANK_5', 'RANK_10', 'QTLU_5', 'QTLD_5']
        # Volume signals
        self.volume_factors = ['VMA_5', 'VMA_10', 'CORR_5', 'CORR_10', 'VSUMP_5', 'VSUMD_5']
        # Volatility signals
        self.volatility_factors = ['STD_5', 'STD_10', 'WVMA_5', 'WVMA_10', 'KMID', 'KLEN']
        
        self.history = deque(maxlen=lookback_periods)
        self.dispersion_history = deque(maxlen=lookback_periods * 5)
    
    def rank_assets(
        self,
        asset_features: dict[str, np.ndarray],  # {symbol: feature_row (1, 158)}
        feature_names: list[str],
    ) -> CrossSectionalSnapshot:
        """Rank multiple assets at current time step.
        
        Args:
            asset_features: {symbol: latest_feature_vector}
            feature_names: list of 158 Alpha158 feature names
        
        Returns:
            CrossSectionalSnapshot with rankings
        """
        if not asset_features:
            raise ValueError("No asset features provided.")
            
        timestamp = datetime.now(timezone.utc).isoformat()
        raw_scores = {}
        for symbol, features in asset_features.items():
            if features.ndim > 1:
                features = features.flatten()
            raw_scores[symbol] = self.compute_composite_alpha(features, feature_names)
            
        symbols = list(raw_scores.keys())
        scores_arr = np.array([raw_scores[s] for s in symbols])
        
        # Cross-sectional z-score
        cs_mean = np.nanmean(scores_arr)
        cs_std = np.nanstd(scores_arr)
        eps = 1e-8
        
        z_scores = (scores_arr - cs_mean) / (cs_std + eps)
        
        # Handle case where all scores are NaN
        if np.isnan(cs_mean) or cs_std == 0:
            z_scores = np.zeros_like(scores_arr)
        else:
            # fill nan z-scores with 0
            z_scores = np.nan_to_num(z_scores, nan=0.0)
            
        dispersion = cs_std
        self.dispersion_history.append(dispersion)
        regime = self.get_regime_from_dispersion(dispersion)
        
        rankings = []
        n_assets = len(symbols)
        
        # Sort indices descending by z_score
        sorted_indices = np.argsort(z_scores)[::-1]
        
        for rank_idx, idx in enumerate(sorted_indices):
            symbol = symbols[idx]
            z = float(z_scores[idx])
            raw = float(scores_arr[idx])
            percentile = (n_assets - 1 - rank_idx) / (n_assets - 1) if n_assets > 1 else 1.0
            is_tradeable = z >= self.min_alpha_zscore
            
            # extract features snapshot
            feat_snap = {}
            features_vec = asset_features[symbol].flatten()
            for f in ['ROC_5', 'RSV_5', 'VMA_5', 'STD_5']:
                if f in feature_names:
                    f_idx = feature_names.index(f)
                    feat_snap[f] = float(features_vec[f_idx])
            
            rankings.append(AssetRanking(
                symbol=symbol,
                rank=rank_idx + 1,
                alpha_score=raw,
                cs_zscore=z,
                percentile=percentile,
                is_tradeable=is_tradeable,
                features_snapshot=feat_snap,
                timestamp=timestamp
            ))
            
        top_asset = rankings[0].symbol if rankings else ""
        spread = float(z_scores[sorted_indices[0]] - z_scores[sorted_indices[-1]]) if n_assets > 1 else 0.0
        n_tradeable = sum(1 for r in rankings if r.is_tradeable)
        
        snapshot = CrossSectionalSnapshot(
            timestamp=timestamp,
            rankings=rankings,
            top_asset=top_asset,
            spread=spread,
            dispersion=float(dispersion),
            n_tradeable=n_tradeable,
            regime_label=regime
        )
        
        self.history.append(snapshot)
        return snapshot
    
    def compute_composite_alpha(
        self,
        features: np.ndarray,  # Shape (158,)
        feature_names: list[str],
    ) -> float:
        """Compute composite alpha score from Alpha158 features."""
        def score_group(factors):
            vals = []
            for f in factors:
                if f in feature_names:
                    idx = feature_names.index(f)
                    val = features[idx]
                    if not np.isnan(val) and not np.isinf(val):
                        vals.append(val)
            if not vals:
                return 0.0
            return float(np.mean(vals))
            
        momentum_score = score_group(self.momentum_factors)
        reversion_score = score_group(self.reversion_factors)
        volume_score = score_group(self.volume_factors)
        volatility_score = score_group(self.volatility_factors)
        
        # Simple composite model
        alpha = (
            self.alpha_weights['momentum'] * momentum_score +
            self.alpha_weights['reversion'] * reversion_score +
            self.alpha_weights['volume'] * volume_score - 
            self.alpha_weights['volatility'] * volatility_score
        )
        return alpha
    
    def get_regime_from_dispersion(self, dispersion: float) -> str:
        """Classify market regime from cross-sectional dispersion."""
        if not self.dispersion_history:
            return 'concentrated'
            
        disp_arr = np.array(self.dispersion_history)
        mean_dispersion = np.mean(disp_arr)
        std_dispersion = np.std(disp_arr)
        
        if std_dispersion == 0:
            return 'concentrated'
            
        if dispersion > mean_dispersion + 1.0 * std_dispersion:
            return 'dispersed'
        elif dispersion < mean_dispersion - 0.5 * std_dispersion:
            return 'flat'
        else:
            return 'concentrated'
    
    def should_trade(self, snapshot: CrossSectionalSnapshot) -> tuple[bool, str]:
        """Determine if the current cross-sectional environment supports trading."""
        if snapshot.regime_label == 'flat':
            return False, "Market is flat, all assets correlated, no edge."
        if snapshot.spread < self.min_spread:
            return False, f"Alpha spread ({snapshot.spread:.4f}) is below minimum ({self.min_spread})."
        if snapshot.n_tradeable == 0:
            return False, "No assets pass the minimum alpha threshold."
        return True, "Trading conditions are favorable."
    
    def get_historical_performance(
        self,
    ) -> dict:
        """Returns performance stats of the ranking system."""
        if not self.history:
            return {}
            
        top_assets = [snap.top_asset for snap in self.history]
        changes = sum(1 for i in range(1, len(top_assets)) if top_assets[i] != top_assets[i-1])
        turnover_rate = changes / (len(top_assets) - 1) if len(top_assets) > 1 else 0.0
        
        return {
            'snapshots_recorded': len(self.history),
            'current_regime': self.history[-1].regime_label,
            'avg_dispersion': float(np.mean(self.dispersion_history)),
            'turnover_rate': turnover_rate,
            'top_asset_persistence': 1.0 - turnover_rate
        }
