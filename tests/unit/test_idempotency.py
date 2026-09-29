"""Tests for SQLite atomic message claim and persistent deduplication."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from trad_auto.command.deduplication import MessageDeduplicator
from trad_auto.core.enums import MessageProcessingState


def test_sqlite_deduplication_lifecycle(tmp_path: Path) -> None:
    """Message deduplication lifecycle: claim -> duplicate blocked -> completed -> cached."""
    db_file = str(tmp_path / "test_dedup.db")
    dedup = MessageDeduplicator(db_path=db_file)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    msg_id = "wamid.12345"

    # 1. First attempt claims successfully
    c1 = dedup.try_claim(msg_id, "WHATSAPP", "+919876543210", now)
    assert c1.claimed is True
    assert c1.state == MessageProcessingState.PROCESSING

    # 2. Duplicate concurrent attempt while PROCESSING is rejected
    c2 = dedup.try_claim(msg_id, "WHATSAPP", "+919876543210", now)
    assert c2.claimed is False
    assert c2.state == MessageProcessingState.PROCESSING

    # 3. Mark completed with response
    dedup.mark_completed(msg_id, "Trading session activated.", now)

    # 4. Third attempt returns cached completed outcome
    c3 = dedup.try_claim(msg_id, "WHATSAPP", "+919876543210", now)
    assert c3.claimed is False
    assert c3.state == MessageProcessingState.COMPLETED
    assert c3.cached_response == "Trading session activated."


def test_sqlite_deduplication_concurrency(tmp_path: Path) -> None:
    """Under high concurrency, exactly one worker can claim the message."""
    db_file = str(tmp_path / "concurrent_dedup.db")
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    msg_id = "wamid.concurrent.999"

    def worker_claim(worker_id: int) -> bool:
        worker_dedup = MessageDeduplicator(db_path=db_file, busy_timeout_ms=5000)
        res = worker_dedup.try_claim(msg_id, "CLI", f"worker_{worker_id}", now)
        return res.claimed

    workers_count = 10
    with ThreadPoolExecutor(max_workers=workers_count) as executor:
        results = list(executor.map(worker_claim, range(workers_count)))

    # Invariant: Exactly one True across all workers
    assert results.count(True) == 1
    assert results.count(False) == workers_count - 1
