"""Microsoft Qlib Alpha158 Factor Library + Cross-Sectional Ranking Engine.

Institutional-grade quantitative feature engineering for Trad-Auto.
"""

from .alpha158 import Alpha158Engine
from .cross_sectional_ranker import (
    CrossSectionalRanker,
    CrossSectionalSnapshot,
    AssetRanking,
)

__all__ = [
    "Alpha158Engine",
    "CrossSectionalRanker",
    "CrossSectionalSnapshot",
    "AssetRanking",
]
