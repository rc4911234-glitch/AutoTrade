"""Repository for persisting and querying TradingSession state."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from trad_auto.core.enums import SessionState, TradingMode
from trad_auto.core.models.session import FinancialLimits, TradingSession
from trad_auto.persistence.database import DatabaseManager


class SessionRepository:
    """Provides atomic persistence and query operations for TradingSession instances."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def save_session(self, session: TradingSession) -> None:
        """Saves or updates a full TradingSession snapshot."""
        limits = session.limits
        now_str = datetime.now(UTC).isoformat()

        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO trading_sessions (
                    session_id, session_number, mode, status,
                    authorized_capital, max_risk_amount, max_exposure,
                    max_allowed_loss, daily_profit_target, deployed_capital,
                    authorized_at, expires_at, owner_command_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status=excluded.status,
                    deployed_capital=excluded.deployed_capital,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (
                    str(session.session_id),
                    session.session_number,
                    session.mode.value,
                    session.status.value,
                    str(limits.authorized_capital),
                    str(limits.max_risk_amount) if limits.max_risk_amount is not None else None,
                    str(limits.max_exposure) if limits.max_exposure is not None else None,
                    str(limits.max_allowed_loss) if limits.max_allowed_loss is not None else None,
                    str(limits.daily_profit_target)
                    if limits.daily_profit_target is not None
                    else None,
                    str(session.deployed_capital),
                    session.authorized_at.isoformat(),
                    session.expires_at.isoformat(),
                    str(session.owner_command_id),
                    now_str,
                ),
            )

    def update_session_status(
        self, session_id: UUID, status: SessionState, timestamp: datetime | None = None
    ) -> bool:
        """Updates the status and updated_at of an existing session."""
        now = (timestamp or datetime.now(UTC)).isoformat()
        with self._db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE trading_sessions
                SET status = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (status.value, now, str(session_id)),
            )
            return cur.rowcount > 0

    def get_session(self, session_id: UUID) -> TradingSession | None:
        """Retrieves a specific TradingSession by ID."""
        with self._db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM trading_sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    def get_active_session(self) -> TradingSession | None:
        """Returns the most recent active session (ARMED, TRADING, PAUSED), if any."""
        with self._db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM trading_sessions
                WHERE status IN ('ARMED', 'TRADING', 'PAUSED')
                ORDER BY session_number DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    def list_all_sessions(self) -> list[TradingSession]:
        """Lists all recorded sessions sorted by session number ascending."""
        with self._db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM trading_sessions ORDER BY session_number ASC"
            ).fetchall()
            return [self._row_to_session(r) for r in rows]

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> TradingSession:
        """Reconstructs a typed TradingSession domain model from a SQLite row."""
        limits = FinancialLimits(
            authorized_capital=Decimal(row["authorized_capital"]),
            max_risk_amount=Decimal(row["max_risk_amount"]) if row["max_risk_amount"] else None,
            max_exposure=Decimal(row["max_exposure"]) if row["max_exposure"] else None,
            max_allowed_loss=Decimal(row["max_allowed_loss"]) if row["max_allowed_loss"] else None,
            daily_profit_target=Decimal(row["daily_profit_target"])
            if row["daily_profit_target"]
            else None,
        )

        return TradingSession(
            session_id=UUID(row["session_id"]),
            session_number=int(row["session_number"]),
            mode=TradingMode(row["mode"]),
            limits=limits,
            deployed_capital=Decimal(row["deployed_capital"]),
            authorized_at=datetime.fromisoformat(row["authorized_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            status=SessionState(row["status"]),
            owner_command_id=UUID(row["owner_command_id"]),
        )
