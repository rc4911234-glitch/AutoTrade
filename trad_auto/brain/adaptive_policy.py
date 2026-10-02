"""Adaptive policy engine dynamically tuning strategy thresholds and risk sizing based on live outcomes."""

from __future__ import annotations

from decimal import Decimal
import logging
import threading
from typing import Any

from trad_auto.brain.models import RegimePerformance, TradeJournalEntry, TradeOutcome
from trad_auto.core.constants import ZERO_DECIMAL

logger = logging.getLogger(__name__)

# Standard recognized regime categories
REGIME_BULLISH = "BULLISH_TREND"
REGIME_BEARISH = "BEARISH_TREND"
REGIME_CHOP = "CHOP_SIDEWAYS"
REGIME_HIGH_VOL = "HIGH_VOLATILITY"
REGIME_UNKNOWN = "UNKNOWN"

ALL_REGIMES = [REGIME_BULLISH, REGIME_BEARISH, REGIME_CHOP, REGIME_HIGH_VOL, REGIME_UNKNOWN]


class AdaptivePolicyEngine:
    """Dynamic reinforcement and penalty engine adjusting risk thresholds based on empirical trade outcomes."""

    def __init__(
        self,
        base_ml_threshold: float = 0.55,
        base_risk_multiplier: float = 1.0,
    ) -> None:
        self._lock = threading.RLock()
        self.base_ml_threshold = base_ml_threshold
        self.base_risk_multiplier = base_risk_multiplier

        # Track stats per regime
        self._regimes: dict[str, RegimePerformance] = {
            reg: RegimePerformance(
                regime=reg,
                current_ml_threshold=base_ml_threshold,
                risk_multiplier=base_risk_multiplier,
            )
            for reg in ALL_REGIMES
        }

        # Rolling streaks
        self._consecutive_losses: dict[str, int] = {reg: 0 for reg in ALL_REGIMES}
        self._consecutive_wins: dict[str, int] = {reg: 0 for reg in ALL_REGIMES}
        self._adaptation_history: list[str] = []

    def _normalize_regime_name(self, raw_regime: str) -> str:
        """Maps diverse regime strings to core canonical categories."""
        if not raw_regime:
            return REGIME_UNKNOWN
        reg = raw_regime.upper()
        if "BULL" in reg or "UPTREND" in reg:
            return REGIME_BULLISH
        if "BEAR" in reg or "DOWNTREND" in reg:
            return REGIME_BEARISH
        if "CHOP" in reg or "RANGE" in reg or "SIDEWAYS" in reg or "CONSOLIDATION" in reg:
            return REGIME_CHOP
        if "VOLATILE" in reg or "NEWS" in reg or "EXPANSION" in reg:
            return REGIME_HIGH_VOL
        return REGIME_UNKNOWN

    def process_trade_outcome(self, entry: TradeJournalEntry) -> str:
        """Processes a closed trade and adaptively tunes parameters."""
        if entry.outcome == TradeOutcome.OPEN:
            return "No adaptation for open trade."

        with self._lock:
            reg_key = self._normalize_regime_name(entry.entry_regime)
            perf = self._regimes.setdefault(
                reg_key,
                RegimePerformance(
                    regime=reg_key,
                    current_ml_threshold=self.base_ml_threshold,
                    risk_multiplier=self.base_risk_multiplier,
                ),
            )

            perf.total_trades += 1
            perf.total_pnl += entry.realized_pnl

            adaptation_msg = ""

            if entry.outcome == TradeOutcome.WIN:
                perf.wins += 1
                self._consecutive_wins[reg_key] = self._consecutive_wins.get(reg_key, 0) + 1
                self._consecutive_losses[reg_key] = 0

                # Reinforce winning setup: relax ML cutoff slightly if win streak >= 3
                if self._consecutive_wins[reg_key] >= 3:
                    # Gradually restore toward baseline or reward momentum
                    perf.current_ml_threshold = max(
                        self.base_ml_threshold,
                        round(perf.current_ml_threshold - 0.02, 2),
                    )
                    perf.risk_multiplier = min(
                        1.15,
                        round(perf.risk_multiplier + 0.05, 2),
                    )
                    adaptation_msg = (
                        f"Win Streak ({self._consecutive_wins[reg_key]}x) in {reg_key}: "
                        f"Maintained confidence (ML cutoff {perf.current_ml_threshold:.2f}, "
                        f"Risk {perf.risk_multiplier:.2f}x)."
                    )
                else:
                    adaptation_msg = f"Recorded WIN in {reg_key}. Win rate now {perf.win_rate_pct:.1f}%."

            elif entry.outcome == TradeOutcome.LOSS:
                perf.losses += 1
                self._consecutive_losses[reg_key] = self._consecutive_losses.get(reg_key, 0) + 1
                self._consecutive_wins[reg_key] = 0

                # Penalty / Defense: Tighten ML requirement and shrink risk size
                new_threshold = min(0.68, round(perf.current_ml_threshold + 0.03, 2))
                new_risk = max(0.40, round(perf.risk_multiplier - 0.15, 2))

                perf.current_ml_threshold = new_threshold
                perf.risk_multiplier = new_risk

                adaptation_msg = (
                    f"Defensive Throttle in {reg_key} ({self._consecutive_losses[reg_key]} consecutive loss): "
                    f"Raised ML threshold to {new_threshold:.2f} (+3%), "
                    f"scaled risk size to {new_risk:.2f}x (-15%) to defend bankroll."
                )

            else:
                adaptation_msg = f"Breakeven trade in {reg_key}. Policy parameters preserved."

            self._adaptation_history.append(adaptation_msg)
            logger.info("AdaptivePolicy: %s", adaptation_msg)
            return adaptation_msg

    def get_threshold_for_regime(self, raw_regime: str) -> float:
        """Returns the current adaptive ML probability threshold for the regime."""
        with self._lock:
            reg_key = self._normalize_regime_name(raw_regime)
            if reg_key in self._regimes:
                return self._regimes[reg_key].current_ml_threshold
            return self.base_ml_threshold

    def get_risk_multiplier_for_regime(self, raw_regime: str) -> float:
        """Returns the current adaptive Kelly/position size multiplier for the regime."""
        with self._lock:
            reg_key = self._normalize_regime_name(raw_regime)
            if reg_key in self._regimes:
                return self._regimes[reg_key].risk_multiplier
            return self.base_risk_multiplier

    def get_all_regime_stats(self) -> dict[str, RegimePerformance]:
        """Returns a snapshot of performance metrics across all market regimes."""
        with self._lock:
            return {k: v for k, v in self._regimes.items()}

    def get_recent_adaptations(self, limit: int = 5) -> list[str]:
        """Returns the latest adaptation events recorded."""
        with self._lock:
            return list(reversed(self._adaptation_history[-limit:]))
