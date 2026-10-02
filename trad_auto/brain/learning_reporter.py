"""Daily learning diary synthesizer and dashboard reporter for continuous trade reflection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import Any

from trad_auto.brain.adaptive_policy import AdaptivePolicyEngine
from trad_auto.brain.models import TradeOutcome
from trad_auto.brain.trade_journal import TradeJournal
from trad_auto.core.constants import ZERO_DECIMAL

logger = logging.getLogger(__name__)


class LearningReporter:
    """Aggregates journal records, post-mortem insights, and adaptive policy states into reports."""

    def __init__(
        self,
        journal: TradeJournal,
        policy_engine: AdaptivePolicyEngine,
    ) -> None:
        self.journal = journal
        self.policy_engine = policy_engine

    def get_dashboard_telemetry(self) -> dict[str, Any]:
        """Provides high-level brain telemetry for Streamlit Section 5."""
        today_trades = self.journal.get_entries_today()
        recent_trades = self.journal.get_recent_entries(limit=15)
        regimes = self.policy_engine.get_all_regime_stats()
        recent_adaptations = self.policy_engine.get_recent_adaptations(limit=5)

        wins = sum(1 for t in today_trades if t.outcome == TradeOutcome.WIN)
        losses = sum(1 for t in today_trades if t.outcome == TradeOutcome.LOSS)
        breakevens = sum(1 for t in today_trades if t.outcome == TradeOutcome.BREAKEVEN)
        net_pnl = sum((t.realized_pnl for t in today_trades), ZERO_DECIMAL)

        win_rate = (wins / len(today_trades) * 100.0) if today_trades else 0.0

        # Collect unique lessons from today or recent trades
        lessons = [
            t.lesson_learned
            for t in (today_trades or recent_trades)
            if t.lesson_learned
        ]
        # Keep unique lessons preserving order
        unique_lessons = list(dict.fromkeys(lessons))[:5]

        regime_matrix = [
            perf.to_dict()
            for perf in regimes.values()
            if perf.total_trades > 0 or perf.regime != "UNKNOWN"
        ]

        return {
            "today_date": datetime.now(UTC).strftime("%Y-%m-%d"),
            "total_today_trades": len(today_trades),
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate_pct": round(win_rate, 1),
            "net_pnl": str(net_pnl),
            "recent_lessons": unique_lessons,
            "recent_adaptations": recent_adaptations,
            "regime_matrix": regime_matrix,
            "recent_entries": [t.to_dict() for t in recent_trades],
        }

    def format_daily_diary_markdown(self) -> str:
        """Produces a clean markdown trader diary entry."""
        telemetry = self.get_dashboard_telemetry()
        lines = [
            f"### 📓 Pro Trader Daily Journal — {telemetry['today_date']}",
            f"**Daily Scorecard:** {telemetry['total_today_trades']} Trades | "
            f"🟢 {telemetry['wins']} Wins | 🔴 {telemetry['losses']} Losses | "
            f"Win Rate: **{telemetry['win_rate_pct']}%** | Net PnL: **${telemetry['net_pnl']} USDT**",
            "",
            "#### 💡 Core Lessons Learned Today",
        ]

        if telemetry["recent_lessons"]:
            for i, lesson in enumerate(telemetry["recent_lessons"], 1):
                lines.append(f"{i}. {lesson}")
        else:
            lines.append("*(No trade post-mortems recorded yet today. Journal is actively monitoring).*")

        lines.extend(["", "#### ⚙️ Adaptive Brain Policy Status"])
        if telemetry["recent_adaptations"]:
            for adapt in telemetry["recent_adaptations"]:
                lines.append(f"- {adapt}")
        else:
            lines.append("- Baseline parameters active: Standard 55% ML signal hurdle, 1.0x risk sizing.")

        return "\n".join(lines)
