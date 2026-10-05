"""Unit tests for Dynamic Regime-Switching Strategy Allocator."""

import pytest

from trad_auto.risk.regime_allocator import DynamicRegimeAllocator


def test_regime_allocator_flat_prioritizes_stat_arb():
    allocator = DynamicRegimeAllocator()
    weights = allocator.evaluate_allocation(cs_regime="flat", market_adx=14.0)

    assert weights.allocator_mode == "PAIRS_MEAN_REVERSION"
    assert weights.stat_arb_weight >= 0.70
    assert weights.scalper_weight <= 0.20
    assert "Statistical Arbitrage" in weights.primary_strategy


def test_regime_allocator_dispersed_prioritizes_scalper():
    allocator = DynamicRegimeAllocator()
    weights = allocator.evaluate_allocation(cs_regime="dispersed", market_adx=35.0)

    assert weights.allocator_mode == "MOMENTUM_HARVEST"
    assert weights.scalper_weight >= 0.70
    assert weights.stat_arb_weight <= 0.25
    assert "Smart Money Scalper" in weights.primary_strategy


def test_regime_allocator_news_freeze_defends_capital():
    allocator = DynamicRegimeAllocator()
    weights = allocator.evaluate_allocation(cs_regime="dispersed", is_news_freeze=True)

    assert weights.allocator_mode == "CAPITAL_PRESERVATION"
    assert weights.scalper_weight == 0.0
    assert weights.stat_arb_weight == 0.0
    assert weights.cash_reserve_weight == 1.0
