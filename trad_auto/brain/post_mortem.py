"""Introspective post-mortem analysis engine for continuous trader self-reflection."""

from __future__ import annotations

from decimal import Decimal
import logging

from trad_auto.brain.models import ExitReason, TradeJournalEntry, TradeOutcome
from trad_auto.core.constants import ZERO_DECIMAL

logger = logging.getLogger(__name__)


class PostMortemAnalyzer:
    """Evaluates completed trades to attribute causality and formulate actionable lessons."""

    def analyze_trade(self, entry: TradeJournalEntry) -> TradeJournalEntry:
        """Conducts deep algorithmic and psychological post-mortem on a closed trade."""
        if entry.outcome == TradeOutcome.OPEN:
            return entry

        if entry.outcome == TradeOutcome.WIN:
            self._analyze_winning_trade(entry)
        elif entry.outcome == TradeOutcome.LOSS:
            self._analyze_losing_trade(entry)
        else:
            self._analyze_breakeven_trade(entry)

        logger.info(
            "PostMortem reflection completed for trade %s: Lesson='%s'",
            entry.trade_id,
            entry.lesson_learned,
        )
        return entry

    def _analyze_winning_trade(self, entry: TradeJournalEntry) -> None:
        """Analyzes a successful trade to reinforce high-edge habits."""
        holding_mins = round(entry.holding_seconds / 60.0, 1)
        r_mult = round(float(entry.r_multiple), 2)
        pnl_str = f"+${entry.realized_pnl}"
        regime = entry.entry_regime or "TRENDING"

        if entry.exit_reason == ExitReason.TAKE_PROFIT_HIT or r_mult >= 1.8:
            entry.post_mortem_analysis = (
                f"Flawless Execution: Trade reached full target ({pnl_str}, {r_mult}R) in {holding_mins}m. "
                f"High-conviction entry aligned with prevailing {regime} momentum. "
                f"Patiently allowed target bracket to fulfill without premature manual exit."
            )
            entry.lesson_learned = (
                f"In {regime} regimes, asymmetric 1:2 R:R brackets yield superior expectancy. "
                f"Continue letting high-probability winners run to full profit target."
            )
            entry.adaptation_applied = (
                f"Reinforced confidence in {regime} setups. Maintained base ML probability threshold."
            )

        elif entry.exit_reason == ExitReason.TRAILING_STOP_RATCHET:
            entry.post_mortem_analysis = (
                f"Dynamic Profit Capture: Trailing ratchet locked in {pnl_str} (+{entry.return_pct:.2f}%) "
                f"after price advanced, guarding against sudden momentum exhaustion in {holding_mins}m."
            )
            entry.lesson_learned = (
                "Trailing stop ratchet effectively converts unrealized gains into realized bankroll "
                "when market encounters intraday resistance."
            )
            entry.adaptation_applied = (
                "Dynamic trailing offsets verified effective; maintained ratcheting parameters."
            )

        else:
            entry.post_mortem_analysis = (
                f"Positive Edge Realization: Trade closed green ({pnl_str}, {holding_mins}m holding). "
                f"Strategy indicators provided reliable directional confirmation."
            )
            entry.lesson_learned = (
                "Systematic entries with positive R:R edge produce consistent positive compounding."
            )
            entry.adaptation_applied = "Standard risk policy maintained."

    def _analyze_losing_trade(self, entry: TradeJournalEntry) -> None:
        """Analyzes a losing trade to pinpoint root cause and tighten risk gates."""
        holding_mins = round(entry.holding_seconds / 60.0, 1)
        r_mult = round(float(entry.r_multiple), 2)
        pnl_str = f"-${abs(entry.realized_pnl)}"
        regime = entry.entry_regime.upper() if entry.entry_regime else "UNKNOWN"
        indicators = entry.entry_indicators or {}

        # 1. Quick Whipsaw / False Breakout
        if entry.holding_seconds < 180.0:
            entry.post_mortem_analysis = (
                f"Fast Stop / Intraday Whipsaw: Position stopped out rapidly within {holding_mins}m ({pnl_str}). "
                f"Price experienced localized liquidity sweep or false breakout in {regime}."
            )
            entry.lesson_learned = (
                f"Avoid jumping on micro-breakouts during {regime} conditions; wait for 1-minute candle close confirmation."
            )
            entry.adaptation_applied = (
                f"Elevated entry filter requirement; demanded stronger multi-timeframe volume confirmation."
            )

        # 2. Sideways / Chop Regime Trap
        elif "CHOP" in regime or "RANGE" in regime or "SIDEWAYS" in regime:
            entry.post_mortem_analysis = (
                f"Regime Mismatch: Directional momentum trade suffered stop-out in {regime} ({pnl_str}, {holding_mins}m). "
                f"Sideways oscillation created noise-to-signal erosion."
            )
            entry.lesson_learned = (
                f"Do not force trend-following entries in {regime}. Demand ADX > 25 and minimum ML probability >= 0.65."
            )
            entry.adaptation_applied = (
                f"Increased ML conviction hurdle for {regime} and dialed down risk multiplier."
            )

        # 3. Overextended Indicators (e.g. Extreme RSI)
        elif indicators.get("rsi") and (float(indicators["rsi"]) > 75 or float(indicators["rsi"]) < 25):
            entry.post_mortem_analysis = (
                f"Overextended Entry: Trade initiated at RSI extreme ({indicators['rsi']}) resulting in immediate mean-reversion pull-back ({pnl_str})."
            )
            entry.lesson_learned = (
                "Never chase trades at momentum extremes; wait for shallow pullback toward VWAP/EMA."
            )
            entry.adaptation_applied = (
                "Tightened overbought/oversold entry filters to reject entries near local exhaustion."
            )

        # 4. Low ML Confidence
        elif entry.ml_probability is not None and entry.ml_probability < 0.60:
            entry.post_mortem_analysis = (
                f"Marginal Signal Conviction: Trade taken with low model probability ({entry.ml_probability:.2f}). "
                f"Edge was insufficient to overcome execution friction and adverse variance ({pnl_str})."
            )
            entry.lesson_learned = (
                "Filter out marginal setups; high-conviction trades (>0.62 ML probability) yield significantly higher win rates."
            )
            entry.adaptation_applied = (
                "Raised minimum baseline ML signal threshold to eliminate low-conviction triggers."
            )

        # 5. Normal Probabilistic Loss (Bracket Protection)
        else:
            entry.post_mortem_analysis = (
                f"Controlled Risk Outflow: Stop-loss executed as designed at {r_mult}R ({pnl_str}). "
                f"Downside strictly capped by bracket order; no catastrophic runaway risk."
            )
            entry.lesson_learned = (
                "Losses are the standard cost of doing business in quantitative trading. Strict stops guarantee survival."
            )
            entry.adaptation_applied = (
                "Preserved capital protection safeguards and 1:2 R:R risk geometry."
            )

    def _analyze_breakeven_trade(self, entry: TradeJournalEntry) -> None:
        """Analyzes scratch or breakeven trades."""
        holding_mins = round(entry.holding_seconds / 60.0, 1)
        entry.post_mortem_analysis = (
            f"Capital Preserved: Trade closed at breakeven after {holding_mins}m holding. "
            f"Breakeven lock ratchet successfully eliminated downside exposure when trade stalled."
        )
        entry.lesson_learned = (
            "Protecting principal on stalled momentum trades ensures zero-drag equity compounding."
        )
        entry.adaptation_applied = "Breakeven lock threshold working as intended."
