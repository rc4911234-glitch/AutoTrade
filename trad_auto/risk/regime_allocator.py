"""Dynamic Regime-Switching Portfolio Allocator (BlackRock Aladdin Inspired).

Dynamically balances risk capital across multiple quantitative strategies:
- High Dispersion / Strong Trend  => 80% SmartMoneyScalper (Capture directional momentum)
- Low Dispersion / Sideways Chop  => 85% Statistical Arbitrage (Harvest cointegrated mean-reversion)
- Extreme Volatility / Macro News  => 100% Capital Defense (Park cash in USDT, pause scalping)
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AllocationWeights:
    """Strategy allocation weights and risk limits for the portfolio."""

    scalper_weight: float  # Fraction of portfolio capital for SmartMoneyScalper (0.0 to 1.0)
    stat_arb_weight: float  # Fraction of portfolio capital for Statistical Arbitrage (0.0 to 1.0)
    cash_reserve_weight: float  # Capital preserved in pure cash buffer
    allocator_mode: str  # 'MOMENTUM_HARVEST' / 'PAIRS_MEAN_REVERSION' / 'CAPITAL_PRESERVATION'
    primary_strategy: str
    rationale: str


class DynamicRegimeAllocator:
    """Manages cross-strategy capital routing based on active market regime."""

    def __init__(self, min_cash_buffer: float = 0.05) -> None:
        self.min_cash_buffer = min_cash_buffer

    def evaluate_allocation(
        self,
        cs_regime: str = "normal",  # 'dispersed', 'concentrated', 'flat'
        market_adx: float = 22.0,  # Trend strength
        is_news_freeze: bool = False,
    ) -> AllocationWeights:
        """Determines optimal strategy weights based on current market microstructure."""
        # 1. Macro News Shield Trigger => 100% Defensive Cash
        if is_news_freeze:
            return AllocationWeights(
                scalper_weight=0.0,
                stat_arb_weight=0.0,
                cash_reserve_weight=1.0,
                allocator_mode="CAPITAL_PRESERVATION",
                primary_strategy="NONE (CASH DEFENSE)",
                rationale="Breaking macro news flash detected. 100% capital parked safely in cash.",
            )

        regime_lower = cs_regime.lower()

        # 2. Flat / Low Dispersion Regime => Pairs Trading Mean-Reversion Priority
        if "flat" in regime_lower or market_adx < 18.0:
            return AllocationWeights(
                scalper_weight=0.10,  # Scalper minimized to stop false-breakout churn
                stat_arb_weight=0.80,  # 80% to Cointegration Stat-Arb
                cash_reserve_weight=0.10,
                allocator_mode="PAIRS_MEAN_REVERSION",
                primary_strategy="Statistical Arbitrage (BTC/ETH Spread)",
                rationale="Market is in sideways chop with low cross-sectional dispersion. Momentum scalping throttled; capital routed to mean-reverting cointegrated pairs.",
            )

        # 3. Dispersed / Strong Trend Regime => Scalper Momentum Priority
        if "dispersed" in regime_lower or market_adx > 30.0:
            return AllocationWeights(
                scalper_weight=0.75,  # 75% to Alpha158 Scalper
                stat_arb_weight=0.20,  # 20% to Stat-Arb
                cash_reserve_weight=0.05,
                allocator_mode="MOMENTUM_HARVEST",
                primary_strategy="Smart Money Scalper (Alpha158 King)",
                rationale="Strong cross-sectional dispersion and trending momentum detected. Aggressive scalping on top-ranked King asset with strict 1:2 R:R brackets.",
            )

        # 4. Concentrated / Normal Baseline Regime => Balanced Split
        return AllocationWeights(
            scalper_weight=0.50,
            stat_arb_weight=0.40,
            cash_reserve_weight=0.10,
            allocator_mode="BALANCED_QUANT",
            primary_strategy="Hybrid Scalper + Stat-Arb",
            rationale="Normal market regime. Capital distributed across Alpha158 Scalper (50%) and Statistical Arbitrage (40%) to ensure risk diversification.",
        )
