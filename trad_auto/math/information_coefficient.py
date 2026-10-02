"""
Quantitative Information Coefficient (IC) & ICIR Evaluation Engine.

Inspired by Microsoft Qlib's `qlib.contrib.eva.alpha` and `SigAnaRecord`.
Evaluates the predictive strength and stability of alpha signals against
future forward returns.

Key Metrics:
- IC (Information Coefficient): Pearson correlation between predicted score and realized return.
- Rank IC (Spearman Rank IC): Monotonic ranking predictive correlation (robust to outliers).
- ICIR (Information Ratio of IC): Mean(IC) / Std(IC), measures consistency across market regimes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
import numpy as np
import scipy.stats as stats


@dataclass(frozen=True)
class AlphaSignalMetrics:
    """Institutional Alpha Signal Evaluation Report."""

    mean_ic: float
    rank_ic: float
    ic_std: float
    icir: float
    p_value: float
    sample_count: int
    is_statistically_significant: bool  # p_value < 0.05 and abs(mean_ic) >= 0.02


def calculate_ic(
    predictions: np.ndarray | list[float],
    actual_returns: np.ndarray | list[float],
) -> tuple[float, float]:
    """
    Computes Pearson Information Coefficient (IC) and its two-tailed p-value.

    Returns:
        (ic, p_value)
    """
    preds = np.asarray(predictions, dtype=np.float64)
    acts = np.asarray(actual_returns, dtype=np.float64)

    if len(preds) < 3 or len(preds) != len(acts):
        return 0.0, 1.0

    # Handle zero variance edge cases
    if np.all(preds == preds[0]) or np.all(acts == acts[0]):
        return 0.0, 1.0

    r, p = stats.pearsonr(preds, acts)
    return float(r) if not math.isnan(r) else 0.0, float(p) if not math.isnan(p) else 1.0


def calculate_rank_ic(
    predictions: np.ndarray | list[float],
    actual_returns: np.ndarray | list[float],
) -> tuple[float, float]:
    """
    Computes Spearman Rank Information Coefficient (Rank IC).

    Measures monotonic ranking alignment, invariant to non-linear scaling.
    """
    preds = np.asarray(predictions, dtype=np.float64)
    acts = np.asarray(actual_returns, dtype=np.float64)

    if len(preds) < 3 or len(preds) != len(acts):
        return 0.0, 1.0

    if np.all(preds == preds[0]) or np.all(acts == acts[0]):
        return 0.0, 1.0

    res = stats.spearmanr(preds, acts)
    r = float(res.statistic) if hasattr(res, "statistic") else float(res[0])
    p = float(res.pvalue) if hasattr(res, "pvalue") else float(res[1])

    return r if not math.isnan(r) else 0.0, p if not math.isnan(p) else 1.0


def calculate_icir(ic_history: list[float] | np.ndarray) -> float:
    """
    Calculates Information Coefficient Information Ratio (ICIR): Mean(IC) / Std(IC).

    Qlib benchmark standard:
    - ICIR > 0.30: Good institutional signal
    - ICIR > 0.50: Elite high-conviction signal
    """
    arr = np.asarray(ic_history, dtype=np.float64)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return 0.0

    std = float(np.std(arr, ddof=1))
    if std <= 1e-12:
        return 0.0

    mean_ic = float(np.mean(arr))
    return float(mean_ic / std)


def evaluate_alpha_stream(
    predictions: np.ndarray | list[float],
    actual_returns: np.ndarray | list[float],
    rolling_window: int = 20,
) -> AlphaSignalMetrics:
    """
    Evaluates full alpha stream metrics across rolling time-slices.
    """
    preds = np.asarray(predictions, dtype=np.float64)
    acts = np.asarray(actual_returns, dtype=np.float64)
    n = len(preds)

    if n < 5:
        return AlphaSignalMetrics(
            mean_ic=0.0,
            rank_ic=0.0,
            ic_std=0.0,
            icir=0.0,
            p_value=1.0,
            sample_count=n,
            is_statistically_significant=False,
        )

    ic, p_val = calculate_ic(preds, acts)
    rank_ic, _ = calculate_rank_ic(preds, acts)

    # Rolling window IC for ICIR
    if n >= rolling_window * 2:
        rolling_ics = []
        for i in range(0, n - rolling_window, rolling_window // 2):
            sub_p = preds[i : i + rolling_window]
            sub_a = acts[i : i + rolling_window]
            sub_ic, _ = calculate_ic(sub_p, sub_a)
            rolling_ics.append(sub_ic)
        ic_std = float(np.std(rolling_ics, ddof=1)) if len(rolling_ics) > 1 else 0.0
        icir = calculate_icir(rolling_ics)
    else:
        ic_std = 0.0
        icir = float(ic / 0.1) if ic != 0 else 0.0

    sig = bool(p_val < 0.05 and abs(ic) >= 0.02)

    return AlphaSignalMetrics(
        mean_ic=round(ic, 4),
        rank_ic=round(rank_ic, 4),
        ic_std=round(ic_std, 4),
        icir=round(icir, 4),
        p_value=round(p_val, 4),
        sample_count=n,
        is_statistically_significant=sig,
    )
