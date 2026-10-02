"""Unit tests for Aladdin-style Value at Risk (VaR) and Stress Testing Engine."""

import numpy as np
import pytest

from trad_auto.risk.aladdin_var import AladdinRiskEngine


def test_aladdin_var_default_small_history() -> None:
    engine = AladdinRiskEngine(max_allowed_var_99_pct=0.05)
    report = engine.evaluate_portfolio(
        equity=1000.0,
        open_notional_exposure=100.0,
        recent_returns=[],
    )
    assert report.portfolio_equity == 1000.0
    assert report.var_95_pct > 0
    assert report.var_99_pct > report.var_95_pct
    assert report.cvar_99_pct >= report.var_99_pct
    assert report.is_safe_to_trade is True
    assert "PASSED" in report.safety_summary


def test_aladdin_var_fat_tailed_returns() -> None:
    np.random.seed(42)
    # Generate student-t or skewed distribution with heavy negative outliers
    rets = np.random.normal(loc=0.0001, scale=0.005, size=100)
    rets[10] = -0.04  # 4% sudden drop
    rets[25] = -0.06  # 6% sudden dump

    engine = AladdinRiskEngine(max_allowed_var_99_pct=0.10)
    report = engine.evaluate_portfolio(
        equity=1000.0,
        open_notional_exposure=200.0,
        recent_returns=rets,
    )
    assert report.skewness < 0  # Negative skew from dumps
    assert report.kurtosis > 0  # Heavy fat tails
    assert report.var_99_pct > 0
    assert report.flash_crash_loss == pytest.approx(200.0 * 0.15)
    assert report.ftx_shock_loss == pytest.approx(200.0 * 0.28)
    assert report.capitulation_loss == pytest.approx(200.0 * 0.45)


def test_aladdin_var_excessive_exposure_rejected() -> None:
    engine = AladdinRiskEngine(max_allowed_var_99_pct=0.05)
    # Massive 10x over-leveraged exposure: 10,000 notional on 1,000 equity
    report = engine.evaluate_portfolio(
        equity=1000.0,
        open_notional_exposure=10000.0,
        recent_returns=[-0.005, 0.002] * 20,
    )
    assert report.is_safe_to_trade is False
    assert "REJECTED" in report.safety_summary
