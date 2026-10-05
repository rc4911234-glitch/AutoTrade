"""Unit tests for Historical Arena Tournament Engine."""

import pytest

from trad_auto.backtest.historical_arena import HistoricalArenaRunner


def test_arena_runner_regimes_defined():
    runner = HistoricalArenaRunner()
    assert len(runner.REGIMES) >= 5
    regime_ids = [r["id"] for r in runner.REGIMES]
    assert "bull_market" in regime_ids
    assert "bear_market" in regime_ids
    assert "sideways_chop" in regime_ids
    assert "crash_shock" in regime_ids


def test_arena_runner_single_regime_execution():
    runner = HistoricalArenaRunner(initial_capital=10000.0)
    chop_regime = next(r for r in runner.REGIMES if r["id"] == "sideways_chop")

    result = runner.run_regime_arena(chop_regime)
    assert result.regime_id == "sideways_chop"
    assert len(result.standings) == 5

    strat_names = [s.strategy_name for s in result.standings]
    assert "Trad-Auto Institutional" in strat_names
    assert "Buy & Hold (BTC)" in strat_names
    assert "Cash / No Trade (USDT)" in strat_names
    assert "Simple EMA 9/21 Trend" in strat_names
    assert "Simple 20-Bar Breakout" in strat_names


def test_arena_runner_tradauto_drawdown_protection():
    runner = HistoricalArenaRunner(initial_capital=10000.0)
    bear_regime = next(r for r in runner.REGIMES if r["id"] == "bear_market")

    result = runner.run_regime_arena(bear_regime)
    tradauto = next(s for s in result.standings if s.strategy_name == "Trad-Auto Institutional")
    buy_hold = next(s for s in result.standings if s.strategy_name == "Buy & Hold (BTC)")

    # Trad-Auto max drawdown must be dramatically smaller than Buy & Hold in a Bear Market
    assert tradauto.max_drawdown_pct < 20.0
    assert buy_hold.max_drawdown_pct > 50.0
    assert tradauto.total_return_pct > buy_hold.total_return_pct
