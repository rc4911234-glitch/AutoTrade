"""SQLite-backed atomic message deduplication repository."""

import sqlite3
from datetime import datetime
from typing import NamedTuple

from trad_auto.core.enums import MessageProcessingState


class ClaimResult(NamedTuple):
    """Result of an atomic message claim attempt."""

    claimed: bool
    state: MessageProcessingState | None
    cached_response: str | None


class MessageDeduplicator:
    """Provides atomic persistent message deduplication via SQLite transactions."""

    def __init__(self, db_path: str = "trad_auto.db", busy_timeout_ms: int = 5000) -> None:
        self._db_path = db_path
        self._busy_timeout_ms = busy_timeout_ms
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=self._busy_timeout_ms / 1000.0)
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        return conn

    def _init_db(self) -> None:
        """Initializes deduplication schema."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    external_message_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    response_payload TEXT,
                    received_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )

    def try_claim(
        self,
        external_message_id: str,
        channel: str,
        sender_id: str,
        received_at: datetime,
    ) -> ClaimResult:
        """Attempts to atomically claim a message.

        Returns ClaimResult(claimed=True, ...) if this worker successfully claimed the message.
        If already processed or in-flight, returns ClaimResult(claimed=False, cached_response=...).
        """
        conn = self._get_connection()
        try:
            with conn:
                # Use immediate transaction for atomic write lock
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT state, response_payload FROM processed_messages "
                    "WHERE external_message_id = ?",
                    (external_message_id,),
                )
                row = cursor.fetchone()

                if row is not None:
                    existing_state = MessageProcessingState(row[0])
                    cached_response = row[1]
                    return ClaimResult(
                        claimed=False,
                        state=existing_state,
                        cached_response=cached_response,
                    )

                cursor.execute(
                    """
                    INSERT INTO processed_messages
                    (external_message_id, channel, sender_id, state, received_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        external_message_id,
                        channel,
                        sender_id,
                        MessageProcessingState.PROCESSING.value,
                        received_at.isoformat(),
                    ),
                )
                return ClaimResult(
                    claimed=True,
                    state=MessageProcessingState.PROCESSING,
                    cached_response=None,
                )
        except sqlite3.IntegrityError:
            # Concurrently claimed between check and insert
            with self._get_connection() as read_conn:
                cursor = read_conn.cursor()
                cursor.execute(
                    "SELECT state, response_payload FROM processed_messages "
                    "WHERE external_message_id = ?",
                    (external_message_id,),
                )
                row = cursor.fetchone()
                if row:
                    return ClaimResult(
                        claimed=False,
                        state=MessageProcessingState(row[0]),
                        cached_response=row[1],
                    )
                return ClaimResult(claimed=False, state=None, cached_response=None)
        finally:
            conn.close()

    def mark_completed(
        self,
        external_message_id: str,
        response_payload: str,
        completed_at: datetime,
    ) -> None:
        """Marks a claimed message as completed and stores the cached response."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE processed_messages
                SET state = ?, response_payload = ?, completed_at = ?
                WHERE external_message_id = ?
                """,
                (
                    MessageProcessingState.COMPLETED.value,
                    response_payload,
                    completed_at.isoformat(),
                    external_message_id,
                ),
            )

    def mark_failed(
        self,
        external_message_id: str,
        error_message: str,
        completed_at: datetime,
    ) -> None:
        """Marks a claimed message as failed with error details."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE processed_messages
                SET state = ?, response_payload = ?, completed_at = ?
                WHERE external_message_id = ?
                """,
                (
                    MessageProcessingState.FAILED.value,
                    error_message,
                    completed_at.isoformat(),
                    external_message_id,
                ),
            )
