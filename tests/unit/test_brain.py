"""Unit tests for the Continuous Learning & Trade Journaling Brain subsystem."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import tempfile
from pathlib import Path
import pytest

from trad_auto.brain.adaptive_policy import (
    AdaptivePolicyEngine,
    REGIME_BULLISH,
    REGIME_CHOP,
)
from trad_auto.brain.learning_reporter import LearningReporter
from trad_auto.brain.models import (
    ExitReason,
    RegimePerformance,
    TradeJournalEntry,
    TradeOutcome,
)
from trad_auto.brain.post_mortem import PostMortemAnalyzer
from trad_auto.brain.trade_journal import TradeJournal
from trad_auto.core.constants import ZERO_DECIMAL


def test_trade_journal_entry_serialization():
    """Verify TradeJournalEntry serializes and deserializes accurately."""
    entry = TradeJournalEntry(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=Decimal("85000.00"),
        exit_price=Decimal("86000.00"),
        quantity=Decimal("0.100"),
        entry_regime="BULLISH_TREND",
        entry_indicators={"rsi": 58.5, "adx": 28.0},
        ml_probability=0.72,
        stop_loss=Decimal("84500.00"),
        take_profit=Decimal("86000.00"),
        outcome=TradeOutcome.WIN,
        exit_reason=ExitReason.TAKE_PROFIT_HIT,
        realized_pnl=Decimal("100.00"),
        return_pct=Decimal("1.18"),
        r_multiple=Decimal("2.00"),
        post_mortem_analysis="Target fulfilled cleanly.",
        lesson_learned="Let winners run.",
    )

    data = entry.to_dict()
    assert data["symbol"] == "BTCUSDT"
    assert data["outcome"] == "WIN"
    assert data["r_multiple"] == "2.00"

    restored = TradeJournalEntry.from_dict(data)
    assert restored.symbol == entry.symbol
    assert restored.entry_price == entry.entry_price
    assert restored.exit_price == entry.exit_price
    assert restored.outcome == TradeOutcome.WIN
    assert restored.exit_reason == ExitReason.TAKE_PROFIT_HIT
    assert restored.lesson_learned == entry.lesson_learned


def test_trade_journal_lifecycle_and_persistence():
    """Verify TradeJournal tracks trade opening, closing, and persists to JSONL."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        journal_file = Path(tmp_dir) / "test_journal.jsonl"
        journal = TradeJournal(filepath=journal_file)

        # 1. Record pre-trade proposal context
        journal.record_proposal_context(
            symbol="BTCUSDT",
            regime="BULLISH_TREND",
            indicators={"rsi": 52.0},
            ml_probability=0.68,
            stop_loss=Decimal("84000.00"),
            take_profit=Decimal("86000.00"),
        )

        # 2. Open position
        now = datetime.now(UTC)
        entry = journal.on_position_opened(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=Decimal("85000.00"),
            quantity=Decimal("0.100"),
            timestamp=now,
        )
        assert entry.outcome == TradeOutcome.OPEN
        assert entry.stop_loss == Decimal("84000.00")
        assert len(journal.get_open_entries()) == 1

        # 3. Close position with profit
        exit_time = now + timedelta(minutes=15)
        closed_entry = journal.on_position_closed(
            symbol="BTCUSDT",
            exit_price=Decimal("86000.00"),
            realized_pnl=Decimal("100.00"),
            timestamp=exit_time,
        )

        assert closed_entry.outcome == TradeOutcome.WIN
        assert closed_entry.exit_reason == ExitReason.TAKE_PROFIT_HIT
        assert closed_entry.r_multiple == Decimal("1.0")  # (100) / (1000 * 0.100) = 1.0
        assert len(journal.get_open_entries()) == 0
        assert len(journal.get_recent_entries()) == 1

        # 4. Verify file was written and can be reloaded
        reloaded_journal = TradeJournal(filepath=journal_file)
        assert len(reloaded_journal.get_recent_entries()) == 1
        assert reloaded_journal.get_recent_entries()[0].realized_pnl == Decimal("100.00")


def test_post_mortem_analyzer_win_and_loss_attribution():
    """Verify PostMortemAnalyzer synthesizes targeted, human-like reflections."""
    analyzer = PostMortemAnalyzer()

    # Winning trade at full target
    win_entry = TradeJournalEntry(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=Decimal("85000.00"),
        exit_price=Decimal("87000.00"),
        quantity=Decimal("0.100"),
        holding_seconds=1200.0,
        entry_regime="BULLISH_TREND",
        outcome=TradeOutcome.WIN,
        exit_reason=ExitReason.TAKE_PROFIT_HIT,
        realized_pnl=Decimal("200.00"),
        r_multiple=Decimal("2.0"),
    )
    analyzed_win = analyzer.analyze_trade(win_entry)
    assert "Flawless Execution" in analyzed_win.post_mortem_analysis
    assert "1:2 R:R brackets yield superior expectancy" in analyzed_win.lesson_learned

    # Loss due to chop / regime mismatch
    chop_loss = TradeJournalEntry(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=Decimal("85000.00"),
        exit_price=Decimal("84500.00"),
        quantity=Decimal("0.100"),
        holding_seconds=600.0,
        entry_regime="CHOP_SIDEWAYS",
        outcome=TradeOutcome.LOSS,
        exit_reason=ExitReason.STOP_LOSS_HIT,
        realized_pnl=Decimal("-50.00"),
        r_multiple=Decimal("-1.0"),
    )
    analyzed_chop = analyzer.analyze_trade(chop_loss)
    assert "Regime Mismatch" in analyzed_chop.post_mortem_analysis
    assert "Do not force trend-following entries in CHOP_SIDEWAYS" in analyzed_chop.lesson_learned


def test_adaptive_policy_tuning():
    """Verify AdaptivePolicyEngine tightens thresholds on losses and reinforces wins."""
    policy = AdaptivePolicyEngine(base_ml_threshold=0.55, base_risk_multiplier=1.0)

    # Initial state
    assert policy.get_threshold_for_regime("CHOP_SIDEWAYS") == 0.55
    assert policy.get_risk_multiplier_for_regime("CHOP_SIDEWAYS") == 1.0

    # Simulate 2 consecutive losses in CHOP
    loss_entry = TradeJournalEntry(
        symbol="BTCUSDT",
        entry_regime="CHOP_SIDEWAYS",
        outcome=TradeOutcome.LOSS,
        realized_pnl=Decimal("-25.00"),
    )
    msg1 = policy.process_trade_outcome(loss_entry)
    assert "Defensive Throttle" in msg1
    assert policy.get_threshold_for_regime("CHOP_SIDEWAYS") == 0.58
    assert policy.get_risk_multiplier_for_regime("CHOP_SIDEWAYS") == 0.85

    policy.process_trade_outcome(loss_entry)
    assert policy.get_threshold_for_regime("CHOP_SIDEWAYS") == 0.61
    assert policy.get_risk_multiplier_for_regime("CHOP_SIDEWAYS") == 0.70

    # Simulate win streak in BULLISH
    win_entry = TradeJournalEntry(
        symbol="BTCUSDT",
        entry_regime="BULLISH_TREND",
        outcome=TradeOutcome.WIN,
        realized_pnl=Decimal("50.00"),
    )
    for _ in range(3):
        policy.process_trade_outcome(win_entry)

    assert policy.get_risk_multiplier_for_regime("BULLISH_TREND") > 1.0


def test_learning_reporter_telemetry():
    """Verify LearningReporter generates valid dashboard telemetry and markdown diary."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        journal = TradeJournal(filepath=Path(tmp_dir) / "test.jsonl")
        policy = AdaptivePolicyEngine()
        analyzer = PostMortemAnalyzer()
        reporter = LearningReporter(journal=journal, policy_engine=policy)

        # Record a trade
        journal.record_proposal_context(
            symbol="BTCUSDT",
            regime="BULLISH_TREND",
            ml_probability=0.75,
            stop_loss=Decimal("84000.00"),
            take_profit=Decimal("86000.00"),
        )
        journal.on_position_opened("BTCUSDT", "LONG", Decimal("85000.00"), Decimal("0.100"))
        closed = journal.on_position_closed("BTCUSDT", Decimal("86000.00"), Decimal("100.00"))
        analyzer.analyze_trade(closed)
        policy.process_trade_outcome(closed)

        telemetry = reporter.get_dashboard_telemetry()
        assert telemetry["total_today_trades"] == 1
        assert telemetry["wins"] == 1
        assert telemetry["win_rate_pct"] == 100.0
        assert len(telemetry["recent_lessons"]) >= 1

        diary = reporter.format_daily_diary_markdown()
        assert "Pro Trader Daily Journal" in diary
        assert "Core Lessons Learned Today" in diary
