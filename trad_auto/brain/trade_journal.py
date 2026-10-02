"""Trade Journaling engine for recording and persisting institutional trade lifecycles."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
import logging
from pathlib import Path
import threading
from typing import Any

from trad_auto.brain.models import ExitReason, TradeJournalEntry, TradeOutcome
from trad_auto.core.constants import ZERO_DECIMAL

logger = logging.getLogger(__name__)


class TradeJournal:
    """Thread-safe persistence engine and episodic memory journal for all trades."""

    def __init__(self, filepath: Path | str = "data/trade_journal.jsonl") -> None:
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._entries: list[TradeJournalEntry] = []
        self._open_entries: dict[str, TradeJournalEntry] = {}
        self._pending_context: dict[str, dict[str, Any]] = {}
        self._load_existing_journal()

    def _load_existing_journal(self) -> None:
        """Loads historical journal entries from the JSONL storage."""
        if not self.filepath.exists():
            return

        with self._lock:
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entry = TradeJournalEntry.from_dict(data)
                            if entry.outcome == TradeOutcome.OPEN:
                                self._open_entries[entry.symbol] = entry
                            else:
                                self._entries.append(entry)
                        except Exception as e:
                            logger.warning("Failed to parse journal line: %s, err=%s", line, e)
                logger.info(
                    "TradeJournal loaded %d closed entries and %d open entries from %s",
                    len(self._entries),
                    len(self._open_entries),
                    self.filepath,
                )
            except Exception as e:
                logger.error("Error reading trade journal %s: %s", self.filepath, e)

    def _append_to_file(self, entry: TradeJournalEntry) -> None:
        """Appends a single entry to JSONL storage."""
        try:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except Exception as e:
            logger.error("Failed to append journal entry %s to disk: %s", entry.trade_id, e)

    def record_proposal_context(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        indicators: dict[str, Any] | None = None,
        ml_probability: float | None = None,
        stop_loss: Decimal = ZERO_DECIMAL,
        take_profit: Decimal = ZERO_DECIMAL,
    ) -> None:
        """Saves pre-trade thesis context emitted by strategy or risk models."""
        with self._lock:
            self._pending_context[symbol] = {
                "regime": regime,
                "indicators": indicators or {},
                "ml_probability": ml_probability,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "timestamp": datetime.now(UTC),
            }

    def on_position_opened(
        self,
        symbol: str,
        side: str,
        entry_price: Decimal,
        quantity: Decimal,
        timestamp: datetime | None = None,
    ) -> TradeJournalEntry:
        """Invoked when a new trade position is confirmed filled."""
        with self._lock:
            ts = timestamp or datetime.now(UTC)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

            ctx = self._pending_context.pop(symbol, {})
            entry = TradeJournalEntry(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                quantity=quantity,
                entry_time=ts,
                entry_regime=ctx.get("regime", "UNKNOWN"),
                entry_indicators=ctx.get("indicators", {}),
                ml_probability=ctx.get("ml_probability"),
                stop_loss=ctx.get("stop_loss", ZERO_DECIMAL),
                take_profit=ctx.get("take_profit", ZERO_DECIMAL),
                outcome=TradeOutcome.OPEN,
            )
            self._open_entries[symbol] = entry
            logger.info("TradeJournal recorded OPEN trade %s for %s @ %s", entry.trade_id, symbol, entry_price)
            return entry

    def on_position_closed(
        self,
        symbol: str,
        exit_price: Decimal,
        realized_pnl: Decimal,
        timestamp: datetime | None = None,
        total_fees: Decimal = ZERO_DECIMAL,
        exit_reason: ExitReason | None = None,
    ) -> TradeJournalEntry:
        """Invoked when a trade is completely closed out."""
        with self._lock:
            ts = timestamp or datetime.now(UTC)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

            entry = self._open_entries.pop(symbol, None)
            if entry is None:
                # Fallback: create entry if opened prior to journal startup
                entry = TradeJournalEntry(
                    symbol=symbol,
                    side="LONG",
                    entry_price=exit_price,
                    quantity=Decimal("0.001"),
                    entry_time=ts,
                )

            entry.exit_price = exit_price
            entry.exit_time = ts
            entry.holding_seconds = max(0.0, (ts - entry.entry_time).total_seconds())
            entry.realized_pnl = realized_pnl

            # Calculate return percentage
            if entry.entry_price > ZERO_DECIMAL:
                if entry.side == "LONG":
                    entry.return_pct = ((exit_price - entry.entry_price) / entry.entry_price) * Decimal("100")
                else:
                    entry.return_pct = ((entry.entry_price - exit_price) / entry.entry_price) * Decimal("100")

            # Calculate R-multiple (PnL divided by initial dollar risk)
            initial_risk_per_unit = abs(entry.entry_price - entry.stop_loss) if entry.stop_loss > ZERO_DECIMAL else ZERO_DECIMAL
            if initial_risk_per_unit > ZERO_DECIMAL and entry.quantity > ZERO_DECIMAL:
                total_initial_risk = initial_risk_per_unit * entry.quantity
                if total_initial_risk > ZERO_DECIMAL:
                    entry.r_multiple = realized_pnl / total_initial_risk

            # Outcome classification
            if realized_pnl > Decimal("0.05"):
                entry.outcome = TradeOutcome.WIN
            elif realized_pnl < Decimal("-0.05"):
                entry.outcome = TradeOutcome.LOSS
            else:
                entry.outcome = TradeOutcome.BREAKEVEN

            # Exit reason resolution
            if exit_reason is not None and exit_reason != ExitReason.UNKNOWN:
                entry.exit_reason = exit_reason
            else:
                entry.exit_reason = self._infer_exit_reason(entry)

            self._entries.append(entry)
            self._append_to_file(entry)
            logger.info(
                "TradeJournal finalized trade %s: Outcome=%s, PnL=%s USDT, R=%s, Reason=%s",
                entry.trade_id,
                entry.outcome.value,
                entry.realized_pnl,
                round(entry.r_multiple, 2),
                entry.exit_reason.value,
            )
            return entry

    def _infer_exit_reason(self, entry: TradeJournalEntry) -> ExitReason:
        """Infers the most likely market mechanism that closed the trade."""
        if entry.exit_price is None:
            return ExitReason.UNKNOWN

        if entry.take_profit > ZERO_DECIMAL:
            # Check if close to take-profit (within 0.15%)
            tp_dist = abs(entry.exit_price - entry.take_profit) / entry.take_profit
            if tp_dist <= Decimal("0.002"):
                return ExitReason.TAKE_PROFIT_HIT

        if entry.stop_loss > ZERO_DECIMAL:
            # Check if close to initial stop-loss
            sl_dist = abs(entry.exit_price - entry.stop_loss) / entry.stop_loss
            if sl_dist <= Decimal("0.002"):
                return ExitReason.STOP_LOSS_HIT

        if entry.outcome == TradeOutcome.WIN and entry.realized_pnl > ZERO_DECIMAL:
            return ExitReason.TRAILING_STOP_RATCHET

        if entry.outcome == TradeOutcome.LOSS:
            return ExitReason.STOP_LOSS_HIT

        return ExitReason.MANUAL_EXIT

    def get_recent_entries(self, limit: int = 20) -> list[TradeJournalEntry]:
        """Returns the most recent completed journal entries, latest first."""
        with self._lock:
            return list(reversed(self._entries[-limit:]))

    def get_open_entries(self) -> list[TradeJournalEntry]:
        """Returns active open positions currently in the journal."""
        with self._lock:
            return list(self._open_entries.values())

    def get_entries_today(self) -> list[TradeJournalEntry]:
        """Returns all trades executed today (UTC)."""
        with self._lock:
            today_utc = datetime.now(UTC).date()
            return [
                e
                for e in self._entries
                if (e.exit_time and e.exit_time.date() == today_utc)
                or (e.entry_time and e.entry_time.date() == today_utc)
            ]
