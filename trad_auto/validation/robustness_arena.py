"""Parameter Robustness, Monte Carlo Permutation, and Execution Stress Engine.

Evaluates strategies against:
1. Parameter Plateau Curvature (detects overfitted parameter cliffs)
2. 10,000-Iteration Monte Carlo Trade Sequence Resampling
3. Execution Reality Stress (1.5x, 2.0x, 3.0x slippage + latency delays)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np


@dataclass
class PlateauTestResult:
    parameter_name: str
    base_value: float
    neighbors: list[float]
    returns_by_val: dict[float, float]
    plateau_stability: float  # 0.0 to 1.0 (1.0 = completely smooth flat ridge)
    is_fragile_cliff: bool
    warning: Optional[str] = None


@dataclass
class MonteCarloResult:
    n_simulations: int
    median_return_pct: float
    worst_5pct_return_pct: float
    max_drawdown_p50: float
    max_drawdown_p95: float
    max_drawdown_p99: float
    longest_losing_streak_p95: int
    probability_of_ruin_pct: float  # Capital drawdown >= 40%
    probability_of_loss_pct: float  # Capital drawdown < 0% at end


@dataclass
class ExecutionStressResult:
    baseline_profit_factor: float
    stress_1_5x_pf: float
    stress_2_0x_pf: float
    stress_3_0x_pf: float
    latency_penalty_pf: float
    survives_2x_stress: bool
    edge_decay_slope: float  # Rate at which edge degrades per unit friction


class RobustnessArena:
    """Institutional Robustness & Stress Engine for Trad-Auto."""

    def __init__(self, random_seed: int = 42) -> None:
        self.rng = np.random.default_rng(random_seed)

    def run_monte_carlo(
        self,
        trade_returns: list[float],
        n_simulations: int = 10000,
        initial_capital: float = 1000.0,
        ruin_drawdown_threshold: float = 0.35,  # 35% drawdown = Ruin
    ) -> MonteCarloResult:
        """Runs 10,000 bootstrap resampled trade sequence simulations."""
        if not trade_returns:
            return MonteCarloResult(
                n_simulations=n_simulations,
                median_return_pct=0.0,
                worst_5pct_return_pct=0.0,
                max_drawdown_p50=0.0,
                max_drawdown_p95=0.0,
                max_drawdown_p99=0.0,
                longest_losing_streak_p95=0,
                probability_of_ruin_pct=0.0,
                probability_of_loss_pct=0.0,
            )

        returns_arr = np.array(trade_returns)
        n_trades = len(returns_arr)

        # Draw (n_simulations, n_trades) indices with replacement
        sample_indices = self.rng.integers(0, n_trades, size=(n_simulations, n_trades))
        sim_returns = returns_arr[sample_indices]  # Shape: (n_sims, n_trades)

        # Equity curves: starting with 1.0 multiplier
        # Return is percentage, e.g. +0.02 for +2%
        cum_multipliers = np.cumprod(1.0 + sim_returns, axis=1)

        # End returns
        final_returns = (cum_multipliers[:, -1] - 1.0) * 100.0

        # Calculate max drawdown for each simulation
        peak = np.maximum.accumulate(cum_multipliers, axis=1)
        drawdowns = (peak - cum_multipliers) / peak  # 0 to 1
        max_drawdowns = np.max(drawdowns, axis=1) * 100.0  # in percent

        # Risk of ruin (any drawdown exceeding threshold)
        ruined_sims = np.any(drawdowns >= ruin_drawdown_threshold, axis=1)
        prob_ruin = float(np.mean(ruined_sims) * 100.0)

        # Probability of ending in loss
        prob_loss = float(np.mean(final_returns < 0.0) * 100.0)

        # Longest losing streak estimation
        # Count consecutive negative returns per simulation
        is_loss = (sim_returns < 0).astype(int)
        # Approximate 95th percentile streak
        streak_samples = []
        for i in range(min(500, n_simulations)):  # Sample 500 sims for speed
            row = is_loss[i]
            max_streak = 0
            curr_streak = 0
            for v in row:
                if v == 1:
                    curr_streak += 1
                    if curr_streak > max_streak:
                        max_streak = curr_streak
                else:
                    curr_streak = 0
            streak_samples.append(max_streak)

        longest_streak_p95 = int(np.percentile(streak_samples, 95)) if streak_samples else 4

        return MonteCarloResult(
            n_simulations=n_simulations,
            median_return_pct=round(float(np.median(final_returns)), 2),
            worst_5pct_return_pct=round(float(np.percentile(final_returns, 5)), 2),
            max_drawdown_p50=round(float(np.median(max_drawdowns)), 2),
            max_drawdown_p95=round(float(np.percentile(max_drawdowns, 95)), 2),
            max_drawdown_p99=round(float(np.percentile(max_drawdowns, 99)), 2),
            longest_losing_streak_p95=longest_streak_p95,
            probability_of_ruin_pct=round(prob_ruin, 2),
            probability_of_loss_pct=round(prob_loss, 2),
        )

    def test_parameter_plateau(
        self,
        parameter_name: str,
        base_value: float,
        neighbor_values: list[float],
        eval_fn: Any,  # Function taking param value -> return_pct
    ) -> PlateauTestResult:
        """Sweeps neighboring parameter values to distinguish robust plateaus from overfit spikes."""
        returns: dict[float, float] = {}
        for val in neighbor_values:
            returns[val] = float(eval_fn(val))

        base_return = returns.get(base_value, 0.0)
        neighbor_rets = [r for v, r in returns.items() if v != base_value]

        if not neighbor_rets or base_return == 0.0:
            return PlateauTestResult(
                parameter_name=parameter_name,
                base_value=base_value,
                neighbors=neighbor_values,
                returns_by_val=returns,
                plateau_stability=0.5,
                is_fragile_cliff=False,
            )

        mean_neighbor = float(np.mean(neighbor_rets))
        min_neighbor = float(np.min(neighbor_rets))

        # Stability: ratio of mean neighbor return to base return
        if base_return > 0:
            stability = max(0.0, min(1.0, mean_neighbor / base_return))
            # If any neighbor drops below zero or collapses by > 50%, it's a cliff
            is_cliff = (min_neighbor < 0.0) or (min_neighbor < base_return * 0.45)
        else:
            stability = 0.0
            is_cliff = True

        warning = None
        if is_cliff:
            warning = f"Overfit Warning: Small parameter drift away from {base_value} causes return collapse to {min_neighbor:.1f}%"

        return PlateauTestResult(
            parameter_name=parameter_name,
            base_value=base_value,
            neighbors=neighbor_values,
            returns_by_val=returns,
            plateau_stability=round(stability, 2),
            is_fragile_cliff=is_cliff,
            warning=warning,
        )

    def test_execution_stress(
        self,
        trades: list[dict[str, Any]],
        base_fee_pct: float = 0.0005,      # 0.05% taker
        base_slippage_pct: float = 0.0003, # 0.03%
    ) -> ExecutionStressResult:
        """Tests strategy resilience against 1.5x, 2.0x, 3.0x slippage and latency delays."""
        if not trades:
            return ExecutionStressResult(
                baseline_profit_factor=1.0,
                stress_1_5x_pf=1.0,
                stress_2_0x_pf=1.0,
                stress_3_0x_pf=1.0,
                latency_penalty_pf=1.0,
                survives_2x_stress=False,
                edge_decay_slope=0.5,
            )

        def _calc_pf(multiplier: float, latency_adj: float = 0.0) -> float:
            wins = 0.0
            losses = 0.0
            for t in trades:
                gross = float(t.get("realized_pnl", 0.0))
                friction = abs(float(t.get("entry_price", 100.0))) * (
                    base_fee_pct * 2 + base_slippage_pct * multiplier + latency_adj
                ) * float(t.get("quantity", 0.05))
                net = gross - friction
                if net > 0:
                    wins += net
                else:
                    losses += abs(net)
            return (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)

        base_pf = _calc_pf(1.0)
        s15_pf = _calc_pf(1.5)
        s20_pf = _calc_pf(2.0)
        s30_pf = _calc_pf(3.0)
        lat_pf = _calc_pf(2.0, latency_adj=0.0002)  # 2x slippage + 2 bps latency penalty

        decay_slope = (base_pf - s20_pf) / base_pf if base_pf > 0 else 1.0

        return ExecutionStressResult(
            baseline_profit_factor=round(base_pf, 2),
            stress_1_5x_pf=round(s15_pf, 2),
            stress_2_0x_pf=round(s20_pf, 2),
            stress_3_0x_pf=round(s30_pf, 2),
            latency_penalty_pf=round(lat_pf, 2),
            survives_2x_stress=(s20_pf >= 1.05),
            edge_decay_slope=round(decay_slope, 3),
        )
