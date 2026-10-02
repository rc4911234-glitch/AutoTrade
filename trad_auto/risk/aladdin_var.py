"""BlackRock Aladdin-Style Value-at-Risk (VaR), CVaR & Stress Testing Risk Engine.

Reference:
- BlackRock Aladdin Enterprise Risk & Analytics Framework.
- Favre, L., & Galeano, J. A. (2002). "Mean-Modified Value-at-Risk Optimization with
  Hedge Funds." Journal of Alternative Investments, 5(2), 21-25.
- Rockafellar, R. T., & Uryasev, S. (2002). "Conditional Value-at-Risk for General
  Loss Distributions." Journal of Banking & Finance, 26(7), 1443-1471.

Institutional Capabilities:
1. Cornish-Fisher Modified VaR (95% & 99% confidence):
   Adjusts critical quantiles for empirical skewness and excess kurtosis (fat tails).
2. Conditional Value-at-Risk (CVaR / Expected Shortfall):
   Estimates expected loss magnitude when tail risk events exceed VaR threshold.
3. Aladdin Crisis Scenario Stress Testing:
   Evaluates portfolio survival across historical crypto tail scenarios (Flash Crash,
   Exchange Run, Systematic Capitulation).
"""

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Sequence
import numpy as np


@dataclass(frozen=True)
class AladdinRiskReport:
    """Institutional portfolio tail risk assessment report."""
    portfolio_equity: float
    var_95_pct: float             # 95% 1-period Value at Risk ($)
    var_99_pct: float             # 99% 1-period Value at Risk ($)
    cvar_99_pct: float            # 99% Expected Shortfall ($)
    skewness: float               # Return distribution skew
    kurtosis: float               # Excess kurtosis (tail fatness)
    flash_crash_loss: float       # Simulated -15% market dump
    ftx_shock_loss: float         # Simulated -28% market contagion
    capitulation_loss: float      # Simulated -45% catastrophic crash
    is_safe_to_trade: bool        # Aladdin safety gate check
    safety_summary: str           # Executive risk summary


class AladdinRiskEngine:
    """Institutional Value-at-Risk (VaR) and Stress Testing Engine."""

    def __init__(
        self,
        max_allowed_var_99_pct: float = 0.05,  # Max allowed 1-period 99% VaR as % of equity
    ) -> None:
        self.max_allowed_var_99_pct = max_allowed_var_99_pct

    def evaluate_portfolio(
        self,
        equity: float | Decimal,
        open_notional_exposure: float | Decimal,
        recent_returns: Sequence[float] | np.ndarray,
    ) -> AladdinRiskReport:
        """Computes modified Cornish-Fisher VaR, CVaR, and multi-scenario stress tests.

        Args:
            equity: Current portfolio total equity in USDT.
            open_notional_exposure: Sum of active position notionals ($).
            recent_returns: Array of recent percentage bar returns.

        Returns:
            AladdinRiskReport containing institutional risk metrics.
        """
        eq = float(equity)
        exp = float(open_notional_exposure)
        rets = np.asarray(recent_returns, dtype=np.float64)

        if len(rets) < 20 or np.all(rets == 0):
            # Baseline conservative estimate if limited returns history
            var_95 = exp * 0.02
            var_99 = exp * 0.035
            cvar_99 = exp * 0.045
            skew = 0.0
            kurt = 0.0
        else:
            mu = float(np.mean(rets))
            sigma = float(np.std(rets))

            if sigma < 1e-8:
                sigma = 0.001

            # Skewness and excess kurtosis
            n = len(rets)
            skew = float(np.sum((rets - mu) ** 3) / (n * (sigma ** 3)))
            kurt = float((np.sum((rets - mu) ** 4) / (n * (sigma ** 4))) - 3.0)

            # Cornish-Fisher expansion for z_alpha:
            # 95% confidence standard z = 1.64485
            # 99% confidence standard z = 2.32635
            z_95_cf = self._cornish_fisher_z(1.64485, skew, kurt)
            z_99_cf = self._cornish_fisher_z(2.32635, skew, kurt)

            # Modified VaR = Exposure * (z_cf * sigma - mu)
            pct_var_95 = max(0.001, z_95_cf * sigma - mu)
            pct_var_99 = max(0.002, z_99_cf * sigma - mu)

            var_95 = exp * pct_var_95
            var_99 = exp * pct_var_99

            # Expected Shortfall (CVaR): average of returns worse than 99% cutoff
            cutoff = -(pct_var_99)
            tail_losses = rets[rets < cutoff]
            if len(tail_losses) > 0:
                cvar_99 = exp * float(abs(np.mean(tail_losses)))
            else:
                cvar_99 = var_99 * 1.30

        # Aladdin Crisis Stress Testing
        flash_crash_loss = exp * 0.15
        ftx_shock_loss = exp * 0.28
        capitulation_loss = exp * 0.45

        # Safety Gate: VaR 99% must not risk more than max allowed percentage of equity
        var_ratio = var_99 / max(eq, 1.0)
        is_safe = var_ratio <= self.max_allowed_var_99_pct

        if is_safe:
            safety_summary = f"PASSED (99% Tail VaR: ${var_99:.2f} / {var_ratio:.1%} of equity)"
        else:
            safety_summary = f"REJECTED (99% Tail VaR ${var_99:.2f} exceeds {self.max_allowed_var_99_pct:.1%} limit)"

        return AladdinRiskReport(
            portfolio_equity=eq,
            var_95_pct=var_95,
            var_99_pct=var_99,
            cvar_99_pct=cvar_99,
            skewness=skew,
            kurtosis=kurt,
            flash_crash_loss=flash_crash_loss,
            ftx_shock_loss=ftx_shock_loss,
            capitulation_loss=capitulation_loss,
            is_safe_to_trade=is_safe,
            safety_summary=safety_summary,
        )

    @staticmethod
    def _cornish_fisher_z(z: float, s: float, k: float) -> float:
        """Cornish-Fisher expansion to modify quantile z for skewness (s) and kurtosis (k)."""
        z_cf = (
            z
            + (1.0 / 6.0) * (z ** 2 - 1.0) * s
            + (1.0 / 24.0) * (z ** 3 - 3.0 * z) * k
            - (1.0 / 36.0) * (2.0 * z ** 3 - 5.0 * z) * (s ** 2)
        )
        return float(np.clip(z_cf, 0.5, 6.0))
