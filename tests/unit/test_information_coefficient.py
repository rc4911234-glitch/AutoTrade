"""Unit tests for the Information Coefficient (IC) and ICIR module."""

import numpy as np
from trad_auto.math.information_coefficient import (
    calculate_ic,
    calculate_rank_ic,
    calculate_icir,
    evaluate_alpha_stream,
)


def test_calculate_ic_perfect_correlation() -> None:
    x = [0.01, 0.02, 0.03, 0.04, 0.05]
    y = [0.02, 0.04, 0.06, 0.08, 0.10]
    ic, p = calculate_ic(x, y)
    assert abs(ic - 1.0) < 1e-4
    assert p < 0.01


def test_calculate_rank_ic_monotonic() -> None:
    # Non-linear monotonic
    x = [1, 2, 3, 4, 5]
    y = [1, 8, 27, 64, 125]
    rank_ic, p = calculate_rank_ic(x, y)
    assert abs(rank_ic - 1.0) < 1e-4
    assert p < 0.01


def test_calculate_ic_flat_edge_case() -> None:
    x = [1.0, 1.0, 1.0]
    y = [1.0, 2.0, 3.0]
    ic, p = calculate_ic(x, y)
    assert ic == 0.0
    assert p == 1.0


def test_calculate_icir() -> None:
    history = [0.05, 0.06, 0.04, 0.05, 0.05]
    icir = calculate_icir(history)
    assert icir > 5.0  # Very tight consistent IC


def test_evaluate_alpha_stream() -> None:
    np.random.seed(42)
    # Synthetic signal with positive correlation to future return
    noise = np.random.normal(0, 0.005, 100)
    actuals = np.random.normal(0, 0.02, 100)
    preds = 0.4 * actuals + noise

    metrics = evaluate_alpha_stream(preds, actuals, rolling_window=20)
    assert metrics.sample_count == 100
    assert metrics.mean_ic > 0.0
    assert metrics.rank_ic > 0.0
    assert metrics.is_statistically_significant is True
