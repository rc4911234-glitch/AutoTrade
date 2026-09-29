"""SQLite database connection manager and schema initialization."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class DatabaseManager:
    """Manages SQLite database connections, WAL mode, foreign keys, and schemas."""

    def __init__(self, db_path: str = "trad_auto.db", busy_timeout_ms: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms

        # Ensure parent directory exists if using a file path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    @property
    def db_path(self) -> str:
        return self._db_path

    def get_connection(self) -> sqlite3.Connection:
        """Returns a configured SQLite connection with foreign keys and busy timeout."""
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
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager executing statements inside an atomic transaction."""
        conn = self.get_connection()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Creates the relational schema and indices if not already present."""
        with self.get_connection() as conn:
            conn.executescript(
                """
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

                CREATE INDEX IF NOT EXISTS idx_orders_status
                    ON orders(status);
                CREATE INDEX IF NOT EXISTS idx_orders_symbol
                    ON orders(symbol);

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
                """
            )
