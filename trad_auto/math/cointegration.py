"""Statistical Arbitrage & Cointegration Engine for Crypto Pairs Trading.

Reference:
- Engle, R. F., & Granger, C. W. (1987). "Co-Integration and Error Correction:
  Representation, Estimation, and Testing." Econometrica, 55(2), 251-276.
- Vidyamurthy, G. (2004). "Pairs Trading: Quantitative Methods and Analysis."
  John Wiley & Sons.

Mathematical Principle:
While individual crypto prices (e.g., BTC, ETH) follow non-stationary random walks I(1),
a linear combination S_t = Y_t - (beta * X_t + alpha) can be stationary I(0).
When the spread deviates significantly from its historical equilibrium (|Z-score| > 2.0),
it exhibits strong mean-reversion characteristics governed by an Ornstein-Uhlenbeck process.
"""

from dataclasses import dataclass
from enum import Enum
import math
from typing import Sequence
import numpy as np


class StatArbSignal(str, Enum):
    """Statistical Arbitrage Pairs Signal."""
    LONG_SPREAD = "LONG_SPREAD"    # Y is underpriced relative to X (Long Y / Short X)
    SHORT_SPREAD = "SHORT_SPREAD"  # Y is overpriced relative to X (Short Y / Long X)
    NEUTRAL = "NEUTRAL"            # Spread is within normal fluctuation bounds
    CLOSE = "CLOSE"                # Spread reverted to equilibrium, close positions


@dataclass(frozen=True)
class PairsSpreadResult:
    """Result of statistical arbitrage cointegration analysis between two assets."""
    symbol_x: str
    symbol_y: str
    hedge_ratio: float       # beta (units of X to hedge 1 unit of Y)
    intercept: float         # alpha
    current_spread: float    # S_t = Y_t - (beta * X_t + alpha)
    spread_mean: float       # rolling mu
    spread_std: float        # rolling sigma
    z_score: float           # (S_t - mu) / sigma
    half_life_bars: float    # Ornstein-Uhlenbeck mean-reversion half life (in bars)
    correlation: float       # Pearson correlation coefficient between X and Y
    signal: StatArbSignal


class CointegrationEngine:
    """Computes cointegrated spread, Z-scores, and Ornstein-Uhlenbeck mean-reversion parameters."""

    def __init__(
        self,
        z_entry_threshold: float = 2.0,
        z_exit_threshold: float = 0.5,
        min_bars_required: int = 30,
    ) -> None:
        """Initializes the cointegration engine.

        Args:
            z_entry_threshold: Z-score threshold to trigger pairs divergence trades (typically 2.0).
            z_exit_threshold: Z-score threshold to close trades upon mean-reversion (typically 0.5).
            min_bars_required: Minimum observations needed for statistical validity.
        """
        self.z_entry_threshold = z_entry_threshold
        self.z_exit_threshold = z_exit_threshold
        self.min_bars_required = min_bars_required

    def analyze_pair(
        self,
        symbol_x: str,
        prices_x: Sequence[float] | np.ndarray,
        symbol_y: str,
        prices_y: Sequence[float] | np.ndarray,
    ) -> PairsSpreadResult | None:
        """Calculates cointegration hedge ratio, spread series, and Z-score.

        Args:
            symbol_x: Base asset symbol (e.g. 'BTCUSDT').
            prices_x: Historical prices of asset X.
            symbol_y: Target asset symbol (e.g. 'ETHUSDT').
            prices_y: Historical prices of asset Y.

        Returns:
            PairsSpreadResult or None if insufficient valid data.
        """
        x = np.asarray(prices_x, dtype=np.float64)
        y = np.asarray(prices_y, dtype=np.float64)

        n = min(len(x), len(y))
        if n < self.min_bars_required:
            return None

        # Align length
        x = x[-n:]
        y = y[-n:]

        # Check for zero or negative values
        if np.any(x <= 0) or np.any(y <= 0):
            return None

        # 1. Pearson Correlation
        std_x = float(np.std(x))
        std_y = float(np.std(y))
        if std_x == 0 or std_y == 0:
            return None

        corr = float(np.corrcoef(x, y)[0, 1])

        # 2. Ordinary Least Squares (OLS) Regression: Y = beta * X + alpha
        # Using numpy polyfit (degree 1)
        beta, alpha = np.polyfit(x, y, deg=1)
        beta = float(beta)
        alpha = float(alpha)

        # 3. Spread Series
        spread = y - (beta * x + alpha)
        current_spread = float(spread[-1])
        spread_mean = float(np.mean(spread))
        spread_std = float(np.std(spread))

        if spread_std < 1e-8:
            z_score = 0.0
        else:
            z_score = float((current_spread - spread_mean) / spread_std)

        # 4. Ornstein-Uhlenbeck Mean-Reversion Half-Life
        # Delta S_t = theta * (mu - S_{t-1}) + eps_t
        # Regress Delta S_t on S_{t-1}
        half_life = self._calculate_half_life(spread)

        # 5. Determine Quantitative Signal
        if z_score >= self.z_entry_threshold:
            # Spread is significantly positive: Y is expensive relative to X
            signal = StatArbSignal.SHORT_SPREAD
        elif z_score <= -self.z_entry_threshold:
            # Spread is significantly negative: Y is cheap relative to X
            signal = StatArbSignal.LONG_SPREAD
        elif abs(z_score) <= self.z_exit_threshold:
            signal = StatArbSignal.CLOSE
        else:
            signal = StatArbSignal.NEUTRAL

        return PairsSpreadResult(
            symbol_x=symbol_x,
            symbol_y=symbol_y,
            hedge_ratio=beta,
            intercept=alpha,
            current_spread=current_spread,
            spread_mean=spread_mean,
            spread_std=spread_std,
            z_score=z_score,
            half_life_bars=half_life,
            correlation=corr,
            signal=signal,
        )

    @staticmethod
    def _calculate_half_life(spread: np.ndarray) -> float:
        """Calculates Ornstein-Uhlenbeck half-life of mean reversion.

        Half-Life = ln(2) / lambda, where lambda is the speed of mean reversion.
        """
        lag_spread = spread[:-1]
        delta_spread = np.diff(spread)

        if len(lag_spread) < 10:
            return 30.0

        # Linear regression of delta_spread on lag_spread: delta_spread = lambda * lag_spread + c
        cov = np.cov(lag_spread, delta_spread)[0, 1]
        var = np.var(lag_spread)

        if var < 1e-8 or cov >= 0:
            # Non-mean-reverting or diverging
            return 999.0

        theta = -cov / var
        if theta <= 0:
            return 999.0

        half_life = math.log(2.0) / theta
        return float(np.clip(half_life, 1.0, 999.0))
