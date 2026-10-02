"""Unit tests verifying Marcos López de Prado's Fractional Differentiation algorithm."""

import numpy as np
import pytest

from trad_auto.math.fractional_diff import FractionalDifferentiator


def test_fractional_weights_convergence():
    fd = FractionalDifferentiator(d=0.40, threshold=1e-4)
    # Weights must start with 1.0 (at lag 0, which is end of reversed array)
    assert fd.weights[-1] == 1.0
    # Lag 1 weight must be -d = -0.40
    assert np.isclose(fd.weights[-2], -0.40)
    # Alternating signs and decaying magnitude
    assert len(fd.weights) > 10
    assert abs(fd.weights[0]) < 1e-3


def test_fractional_diff_constant_series():
    # A flat constant series differentiated fractionally should decay toward zero
    fd = FractionalDifferentiator(d=0.50, threshold=1e-3)
    prices = np.full(50, 100.0)
    res = fd.transform(prices)
    # Output must have same length as input
    assert len(res) == 50
    # Values must be finite and non-NaN
    assert not np.isnan(res).any()
    assert not np.isinf(res).any()


def test_fractional_diff_preserves_memory():
    fd = FractionalDifferentiator(d=0.40, threshold=1e-4)
    mem_ratio = fd.get_memory_weight_ratio()
    # Memory ratio for d=0.40 must be between 30% and 60%
    assert 0.30 <= mem_ratio <= 0.60


def test_fractional_diff_trend_direction():
    fd = FractionalDifferentiator(d=0.40, threshold=1e-4)
    bull_prices = np.linspace(50000, 55000, 80)
    bear_prices = np.linspace(55000, 50000, 80)

    bull_trans = fd.transform(bull_prices)
    bear_trans = fd.transform(bear_prices)

    # Bullish trend must produce positive fractional drift
    assert bull_trans[-1] > 0.0
    # Bearish trend must produce negative fractional drift
    assert bear_trans[-1] < 0.0
