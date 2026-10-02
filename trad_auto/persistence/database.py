"""Database connection manager and schema initialization supporting SQLite and Supabase PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3
from typing import Any

try:
    import psycopg2
    import psycopg2.extras
    _HAS_POSTGRES = True
except ImportError:
    _HAS_POSTGRES = False

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
-- 1. Trading Sessions
CREATE TABLE IF NOT EXISTS trading_sessions (
    session_id TEXT PRIMARY KEY,
    session_number INTEGER NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    authorized_capital TEXT NOT NULL,
    max_risk_amount TEXT,
    max_exposure TEXT,
    max_allowed_loss TEXT,
    daily_profit_target TEXT,
    deployed_capital TEXT NOT NULL,
    authorized_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    owner_command_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 2. Multi-Currency Portfolio Balances
CREATE TABLE IF NOT EXISTS portfolio_balances (
    asset TEXT PRIMARY KEY,
    free TEXT NOT NULL,
    locked TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 3. Position Lots (FIFO lot tracking)
CREATE TABLE IF NOT EXISTS position_lots (
    lot_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    initial_quantity TEXT NOT NULL,
    remaining_quantity TEXT NOT NULL,
    fee_paid TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    is_closed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_position_lots_symbol_open
    ON position_lots(symbol, is_closed);

-- 4. Order Lifecycle
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    filled_quantity TEXT NOT NULL,
    status TEXT NOT NULL,
    action_purpose TEXT NOT NULL,
    time_in_force TEXT NOT NULL,
    intent_id TEXT,
    parent_order_id TEXT,
    rejection_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);

-- 5. Completed Round-Trip Trades
CREATE TABLE IF NOT EXISTS completed_trades (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    exit_price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    fees TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    holding_duration_seconds TEXT NOT NULL
);

-- 6. Append-Only Audit Trail
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_events_type_time
    ON audit_events(event_type, timestamp);

-- 7. Continuous Learning & Trade Journal
CREATE TABLE IF NOT EXISTS trade_journal (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    exit_price TEXT,
    quantity TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    holding_seconds REAL DEFAULT 0.0,
    entry_regime TEXT DEFAULT 'UNKNOWN',
    entry_indicators TEXT DEFAULT '{}',
    ml_probability REAL,
    stop_loss TEXT,
    take_profit TEXT,
    outcome TEXT NOT NULL,
    exit_reason TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    return_pct TEXT NOT NULL,
    r_multiple TEXT NOT NULL,
    post_mortem_analysis TEXT,
    lesson_learned TEXT,
    adaptation_applied TEXT
);
CREATE INDEX IF NOT EXISTS idx_trade_journal_entry_time ON trade_journal(entry_time);
CREATE INDEX IF NOT EXISTS idx_trade_journal_outcome ON trade_journal(outcome);
"""


class PostgresCursorWrapper:
    """Wraps a psycopg2 cursor to provide seamless sqlite-like query execution."""

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def execute(self, sql: str, params: Any = None) -> PostgresCursorWrapper:
        pg_sql = sql.replace("?", "%s")
        self._cur.execute(pg_sql, params or ())
        return self

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def close(self) -> None:
        self._cur.close()


class PostgresConnectionWrapper:
    """Wraps a psycopg2 connection to mimic sqlite3.Connection interface."""

    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn

    def execute(self, sql: str, params: Any = None) -> PostgresCursorWrapper:
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        wrapper = PostgresCursorWrapper(cur)
        return wrapper.execute(sql, params)

    def executescript(self, sql: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql)
        self._conn.commit()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PostgresConnectionWrapper:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()


class DatabaseManager:
    """Manages database connections, schemas, and transactions for SQLite or Supabase PostgreSQL."""

    def __init__(
        self,
        db_path: str = "trad_auto.db",
        busy_timeout_ms: int = 5000,
        database_url: str = "",
    ) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._database_url = database_url.strip() if database_url else ""
        self._is_postgres = bool(
            self._database_url
            and (self._database_url.startswith("postgresql://") or self._database_url.startswith("postgres://"))
            and _HAS_POSTGRES
        )

        if self._is_postgres:
            logger.info("DatabaseManager initialized with Supabase PostgreSQL backend.")
        else:
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            logger.info("DatabaseManager initialized with local SQLite backend: %s", db_path)

        self._init_db()

    @property
    def is_postgres(self) -> bool:
        """Returns True if connected to Supabase PostgreSQL."""
        return self._is_postgres

    @property
    def db_path(self) -> str:
        return self._db_path

    def get_connection(self) -> Any:
        """Returns an active database connection configured with dictionary row access."""
        if self._is_postgres:
            raw = psycopg2.connect(self._database_url)
            return PostgresConnectionWrapper(raw)

        conn = sqlite3.connect(
            self._db_path,
            timeout=self._busy_timeout_ms / 1000.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys = ON")

        if self._db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")

        return conn

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """Context manager executing statements inside an atomic transaction."""
        conn = self.get_connection()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Creates the relational schema and indices if not already present."""
        try:
            with self.get_connection() as conn:
                conn.executescript(SCHEMA_SQL)
            logger.info("DatabaseManager schema initialization completed successfully.")
        except Exception as err:
            logger.error("DatabaseManager schema init failed: %s", err)
            if self._is_postgres:
                # Safe fallback to SQLite if network or credentials error occurs
                logger.warning("Failing back from PostgreSQL to local SQLite storage.")
                self._is_postgres = False
                if self._db_path != ":memory:":
                    Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
                with self.get_connection() as conn:
                    conn.executescript(SCHEMA_SQL)
