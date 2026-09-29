"""Repository for persisting balances and FIFO position lots."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from trad_auto.core.enums import PositionSide
from trad_auto.core.models.portfolio import Balance, PositionLot
from trad_auto.persistence.database import DatabaseManager


class PortfolioRepository:
    """Provides atomic persistence and query operations for balances and FIFO lots."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def save_balance(self, balance: Balance, timestamp: datetime | None = None) -> None:
        """Saves or updates an asset's free and locked balances."""
        now_str = (timestamp or datetime.now(UTC)).isoformat()
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_balances (asset, free, locked, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(asset) DO UPDATE SET
                    free=excluded.free,
                    locked=excluded.locked,
                    updated_at=excluded.updated_at
                """,
                (balance.asset, str(balance.free), str(balance.locked), now_str),
            )

    def get_balance(self, asset: str) -> Balance | None:
        """Retrieves balance for a specific asset."""
        with self._db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM portfolio_balances WHERE asset = ?",
                (asset,),
            ).fetchone()
            if row is None:
                return None
            return Balance(
                asset=row["asset"],
                free=Decimal(row["free"]),
                locked=Decimal(row["locked"]),
            )

    def load_all_balances(self) -> list[Balance]:
        """Loads all persisted asset balances."""
        with self._db.get_connection() as conn:
            rows = conn.execute("SELECT * FROM portfolio_balances ORDER BY asset ASC").fetchall()
            return [
                Balance(
                    asset=r["asset"],
                    free=Decimal(r["free"]),
                    locked=Decimal(r["locked"]),
                )
                for r in rows
            ]

    def save_lot(self, lot: PositionLot) -> None:
        """Persists a new or modified FIFO position lot."""
        is_closed = 1 if lot.remaining_quantity <= Decimal("0") else 0
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO position_lots (
                    lot_id, symbol, side, entry_price, initial_quantity,
                    remaining_quantity, fee_paid, timestamp, is_closed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lot_id) DO UPDATE SET
                    remaining_quantity=excluded.remaining_quantity,
                    is_closed=excluded.is_closed
                """,
                (
                    str(lot.lot_id),
                    lot.symbol,
                    lot.side.value,
                    str(lot.entry_price),
                    str(lot.initial_quantity),
                    str(lot.remaining_quantity),
                    str(lot.fee_paid),
                    lot.timestamp.isoformat(),
                    is_closed,
                ),
            )

    def update_lot_remaining_quantity(self, lot_id: UUID, remaining_quantity: Decimal) -> bool:
        """Updates remaining quantity and marks closed if 0."""
        is_closed = 1 if remaining_quantity <= Decimal("0") else 0
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE position_lots
                SET remaining_quantity = ?, is_closed = ?
                WHERE lot_id = ?
                """,
                (str(remaining_quantity), is_closed, str(lot_id)),
            )
            return cur.rowcount > 0

    def load_open_lots(self, symbol: str | None = None) -> list[PositionLot]:
        """Loads all open position lots (remaining_quantity > 0) in FIFO order."""
        with self._db.get_connection() as conn:
            if symbol is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM position_lots
                    WHERE symbol = ? AND is_closed = 0
                    ORDER BY timestamp ASC
                    """,
                    (symbol,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM position_lots
                    WHERE is_closed = 0
                    ORDER BY timestamp ASC
                    """
                ).fetchall()

            return [self._row_to_lot(r) for r in rows]

    def load_all_lots(self, symbol: str | None = None) -> list[PositionLot]:
        """Loads all lots (open and closed) ordered by timestamp."""
        with self._db.get_connection() as conn:
            if symbol is not None:
                rows = conn.execute(
                    "SELECT * FROM position_lots WHERE symbol = ? ORDER BY timestamp ASC",
                    (symbol,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM position_lots ORDER BY timestamp ASC").fetchall()

            return [self._row_to_lot(r) for r in rows]

    @staticmethod
    def _row_to_lot(row: sqlite3.Row) -> PositionLot:
        """Converts a SQLite row into a typed PositionLot."""
        return PositionLot(
            lot_id=UUID(row["lot_id"]),
            symbol=row["symbol"],
            side=PositionSide(row["side"]),
            entry_price=Decimal(row["entry_price"]),
            initial_quantity=Decimal(row["initial_quantity"]),
            remaining_quantity=Decimal(row["remaining_quantity"]),
            fee_paid=Decimal(row["fee_paid"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )
