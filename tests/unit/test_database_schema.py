"""Unit tests for SQLite DatabaseManager and schema initialization."""

from pathlib import Path

import pytest

from trad_auto.persistence.database import DatabaseManager


class TestDatabaseManagerInit:
    """Connection initialization and schema tests."""

    def test_db_creates_all_tables(self, tmp_path: Path) -> None:
        db_file = str(tmp_path / "test_schema.db")
        db = DatabaseManager(db_path=db_file)
        with db.get_connection() as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]

        expected_tables = {
            "audit_events",
            "completed_trades",
            "orders",
            "portfolio_balances",
            "position_lots",
            "trading_sessions",
        }
        for t in expected_tables:
            assert t in tables

    def test_file_db_enables_wal_mode(self, tmp_path: Path) -> None:
        db_file = str(tmp_path / "test_wal.db")
        db = DatabaseManager(db_path=db_file)

        with db.get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.upper() == "WAL"

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        db_file = str(tmp_path / "test_fk.db")
        db = DatabaseManager(db_path=db_file)
        with db.get_connection() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1

    def test_transaction_rollback_on_error(self, tmp_path: Path) -> None:
        db_file = str(tmp_path / "test_rollback.db")
        db = DatabaseManager(db_path=db_file)

        with pytest.raises(RuntimeError, match="Force rollback"):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO portfolio_balances VALUES ('USDT', '1000', '0', '2025-01-01')"
                )
                raise RuntimeError("Force rollback")

        # Verify nothing was inserted
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM portfolio_balances WHERE asset = 'USDT'").fetchone()
            assert row is None
