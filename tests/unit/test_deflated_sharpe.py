"""Unit tests for Deflated Sharpe Ratio (DSR) and PSR engine."""

import numpy as np
import pytest

from trad_auto.math.deflated_sharpe import DeflatedSharpeEngine


def test_deflated_sharpe_zero_or_small_returns() -> None:
    analytics = DeflatedSharpeEngine.analyze([])
    assert analytics.observed_sharpe == 0.0
    assert analytics.is_statistically_significant is False


def test_deflated_sharpe_random_walk_rejected() -> None:
    np.random.seed(42)
    # Zero alpha random noise returns
    noise_returns = np.random.normal(loc=0.0, scale=0.01, size=200)
    analytics = DeflatedSharpeEngine.analyze(noise_returns, num_trials=50)

    # Random noise should NOT be statistically significant
    assert analytics.is_statistically_significant is False
    assert analytics.deflated_sharpe_prob < 0.95


def test_deflated_sharpe_genuine_alpha_accepted() -> None:
    np.random.seed(42)
    # Strong positive persistent alpha with high Sharpe
    alpha_returns = np.random.normal(loc=0.005, scale=0.004, size=300)
    analytics = DeflatedSharpeEngine.analyze(alpha_returns, num_trials=10, sharpe_variance=0.2)

    assert analytics.observed_sharpe > 0.8
    assert analytics.psr_prob > 0.99
    assert analytics.deflated_sharpe_prob >= 0.95
    assert analytics.is_statistically_significant is True
