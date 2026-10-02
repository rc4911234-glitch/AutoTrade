"""Marcos López de Prado's Deflated Sharpe Ratio (DSR) and Probabilistic Sharpe Ratio (PSR).

Reference:
- Bailey, D. H., & López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting
  for Selection Bias, Backtest Overfitting and Non-Normality."
  Journal of Portfolio Management, 40(5), 94-107.

Institutional Principle:
Standard backtests and quantitative models are prone to p-hacking (selection bias).
The Deflated Sharpe Ratio discounts the observed Sharpe Ratio based on:
1. The number of trials/strategies tested (N).
2. The variance of Sharpe ratios across those trials.
3. The sample length (T).
4. Return skewness and excess kurtosis (non-normality).
"""

from dataclasses import dataclass
import math
from typing import Sequence
import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class SharpeAnalytics:
    """Institutional Sharpe ratio statistical analysis report."""
    observed_sharpe: float
    annualized_sharpe: float
    psr_prob: float            # Probabilistic Sharpe Ratio vs benchmark
    deflated_sharpe_prob: float # Deflated Sharpe Ratio accounting for N trials
    is_statistically_significant: bool # True if DSR >= 0.95 (p < 0.05)
    skewness: float
    kurtosis: float
    num_observations: int


class DeflatedSharpeEngine:
    """Calculates Probabilistic and Deflated Sharpe Ratios to reject overfitted backtests."""

    @classmethod
    def analyze(
        cls,
        returns: Sequence[float] | np.ndarray,
        benchmark_sharpe: float = 0.0,
        num_trials: int = 50,
        sharpe_variance: float = 0.5,
        annualization_factor: float = math.sqrt(365 * 24 * 60), # 1-min crypto bars
    ) -> SharpeAnalytics:
        """Computes PSR and DSR for an array of trading returns."""
        rets = np.asarray(returns, dtype=np.float64)
        n = len(rets)

        if n < 10 or np.all(rets == 0):
            return SharpeAnalytics(
                observed_sharpe=0.0,
                annualized_sharpe=0.0,
                psr_prob=0.50,
                deflated_sharpe_prob=0.50,
                is_statistically_significant=False,
                skewness=0.0,
                kurtosis=0.0,
                num_observations=n,
            )

        mu = float(np.mean(rets))
        sigma = float(np.std(rets, ddof=1))

        if sigma < 1e-8:
            sr = 0.0
            skew = 0.0
            kurt = 0.0
        else:
            sr = mu / sigma
            skew = float(np.sum((rets - mu) ** 3) / (n * (sigma ** 3)))
            kurt = float((np.sum((rets - mu) ** 4) / (n * (sigma ** 4))) - 3.0)

        # 1. Standard Error of Sharpe Ratio (Mertens formula for non-normal returns)
        denom_sq = 1.0 - (skew * sr) + (((kurt - 1.0) / 4.0) * (sr ** 2))
        sr_std = math.sqrt(max(0.0001, denom_sq) / max(1, n - 1))

        # 2. Probabilistic Sharpe Ratio (PSR) vs benchmark
        z_psr = (sr - benchmark_sharpe) / sr_std
        psr = float(norm.cdf(z_psr))

        # 3. Expected Maximum Sharpe Ratio under null hypothesis of no true alpha
        # E[max(SR_N)] approx = sqrt(V) * ((1 - euler_mascheroni) * Z^-1(1 - 1/N) + euler * Z^-1(1 - 1/(N*e)))
        euler_mascheroni = 0.5772156649
        z_p1 = norm.ppf(1.0 - (1.0 / max(2, num_trials)))
        z_p2 = norm.ppf(1.0 - (1.0 / (max(2, num_trials) * math.e)))
        expected_max_sr = math.sqrt(sharpe_variance) * (
            (1.0 - euler_mascheroni) * z_p1 + euler_mascheroni * z_p2
        )

        # 4. Deflated Sharpe Ratio (DSR)
        z_dsr = (sr - expected_max_sr) / sr_std
        dsr = float(norm.cdf(z_dsr))

        return SharpeAnalytics(
            observed_sharpe=round(sr, 4),
            annualized_sharpe=round(sr * annualization_factor, 2),
            psr_prob=round(psr, 4),
            deflated_sharpe_prob=round(dsr, 4),
            is_statistically_significant=dsr >= 0.95,
            skewness=round(skew, 2),
            kurtosis=round(kurt, 2),
            num_observations=n,
        )
