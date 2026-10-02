"""Data models and enums for the Continuous Learning & Trade Journaling Brain."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from trad_auto.core.constants import ZERO_DECIMAL


class TradeOutcome(str, Enum):
    """Categorized result of a closed trade."""

    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    OPEN = "OPEN"


class ExitReason(str, Enum):
    """The market mechanism that triggered trade closure."""

    TAKE_PROFIT_HIT = "TAKE_PROFIT_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    TRAILING_STOP_RATCHET = "TRAILING_STOP_RATCHET"
    ORPHAN_SHIELD_FLATTEN = "ORPHAN_SHIELD_FLATTEN"
    MANUAL_EXIT = "MANUAL_EXIT"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    UNKNOWN = "UNKNOWN"


@dataclass
class TradeJournalEntry:
    """Comprehensive chronicle of a trade from thesis to post-mortem reflection."""

    trade_id: str = field(default_factory=lambda: uuid4().hex[:12])
    symbol: str = "BTCUSDT"
    side: str = "LONG"
    entry_price: Decimal = ZERO_DECIMAL
    exit_price: Decimal | None = None
    quantity: Decimal = ZERO_DECIMAL
    entry_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    exit_time: datetime | None = None
    holding_seconds: float = 0.0

    # Macro & Technical Context at Entry
    entry_regime: str = "UNKNOWN"
    entry_indicators: dict[str, Any] = field(default_factory=dict)
    ml_probability: float | None = None
    stop_loss: Decimal = ZERO_DECIMAL
    take_profit: Decimal = ZERO_DECIMAL

    # Outcome & Performance
    outcome: TradeOutcome = TradeOutcome.OPEN
    exit_reason: ExitReason = ExitReason.UNKNOWN
    realized_pnl: Decimal = ZERO_DECIMAL
    return_pct: Decimal = ZERO_DECIMAL
    r_multiple: Decimal = ZERO_DECIMAL

    # Human Trader Self-Reflection & Machine Learning
    post_mortem_analysis: str = ""
    lesson_learned: str = ""
    adaptation_applied: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serializes entry to JSON-compatible dictionary."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price) if self.exit_price is not None else None,
            "quantity": str(self.quantity),
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat() if self.exit_time is not None else None,
            "holding_seconds": round(self.holding_seconds, 1),
            "entry_regime": self.entry_regime,
            "entry_indicators": self.entry_indicators,
            "ml_probability": self.ml_probability,
            "stop_loss": str(self.stop_loss),
            "take_profit": str(self.take_profit),
            "outcome": self.outcome.value,
            "exit_reason": self.exit_reason.value,
            "realized_pnl": str(self.realized_pnl),
            "return_pct": str(self.return_pct),
            "r_multiple": str(self.r_multiple),
            "post_mortem_analysis": self.post_mortem_analysis,
            "lesson_learned": self.lesson_learned,
            "adaptation_applied": self.adaptation_applied,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradeJournalEntry":
        """Deserializes dictionary back into a TradeJournalEntry."""
        return cls(
            trade_id=data.get("trade_id", uuid4().hex[:12]),
            symbol=data.get("symbol", "BTCUSDT"),
            side=data.get("side", "LONG"),
            entry_price=Decimal(str(data.get("entry_price", "0"))),
            exit_price=Decimal(str(data["exit_price"])) if data.get("exit_price") else None,
            quantity=Decimal(str(data.get("quantity", "0"))),
            entry_time=datetime.fromisoformat(data["entry_time"]) if "entry_time" in data else datetime.now(UTC),
            exit_time=datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None,
            holding_seconds=float(data.get("holding_seconds", 0.0)),
            entry_regime=data.get("entry_regime", "UNKNOWN"),
            entry_indicators=data.get("entry_indicators", {}),
            ml_probability=data.get("ml_probability"),
            stop_loss=Decimal(str(data.get("stop_loss", "0"))),
            take_profit=Decimal(str(data.get("take_profit", "0"))),
            outcome=TradeOutcome(data.get("outcome", TradeOutcome.OPEN.value)),
            exit_reason=ExitReason(data.get("exit_reason", ExitReason.UNKNOWN.value)),
            realized_pnl=Decimal(str(data.get("realized_pnl", "0"))),
            return_pct=Decimal(str(data.get("return_pct", "0"))),
            r_multiple=Decimal(str(data.get("r_multiple", "0"))),
            post_mortem_analysis=data.get("post_mortem_analysis", ""),
            lesson_learned=data.get("lesson_learned", ""),
            adaptation_applied=data.get("adaptation_applied", ""),
        )


@dataclass
class RegimePerformance:
    """Rolling statistical learning performance tracked per market regime."""

    regime: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: Decimal = ZERO_DECIMAL
    current_ml_threshold: float = 0.55  # Adaptive baseline
    risk_multiplier: float = 1.0        # Adaptive position sizing scaler

    @property
    def win_rate_pct(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.wins / self.total_trades) * 100.0

    @property
    def profit_factor(self) -> float:
        # Simplified ratio of wins over losses
        if self.losses == 0:
            return 3.0 if self.wins > 0 else 1.0
        return max(0.1, round(self.wins / self.losses, 2))

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": round(self.win_rate_pct, 1),
            "total_pnl": str(self.total_pnl),
            "profit_factor": self.profit_factor,
            "current_ml_threshold": round(self.current_ml_threshold, 2),
            "risk_multiplier": round(self.risk_multiplier, 2),
        }
