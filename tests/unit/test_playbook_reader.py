"""Unit tests for Pro Trader Playbook Reader and Visual Trade Replay."""

import pytest

from trad_auto.brain.playbook_reader import (
    build_daily_pro_notes,
    create_trade_replay_figure,
    get_available_dates,
)


def test_get_available_dates():
    sample_trades = [
        {"entry_time": "2026-10-05T12:00:00Z"},
        {"entry_time": "2026-10-05T15:00:00Z"},
        {"entry_time": "2026-10-04T08:00:00Z"},
    ]
    dates = get_available_dates(sample_trades)
    assert dates == ["2026-10-05", "2026-10-04"]


def test_build_daily_pro_notes():
    sample_trades = [
        {
            "trade_id": "t1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "outcome": "WIN",
            "realized_pnl": 2.50,
            "lesson_learned": "Let winners run with 1:2 R:R brackets.",
        },
        {
            "trade_id": "t2",
            "symbol": "ETHUSDT",
            "side": "SHORT",
            "outcome": "LOSS",
            "realized_pnl": -1.25,
            "lesson_learned": "Wait for candle close confirmation.",
        },
    ]
    notes = build_daily_pro_notes("2026-10-05", sample_trades)
    assert notes["total_trades"] == 2
    assert notes["wins"] == 1
    assert notes["losses"] == 1
    assert notes["win_rate"] == 50.0
    assert notes["net_pnl"] == 1.25
    assert len(notes["lessons"]) == 2


def test_create_trade_replay_figure():
    trade = {
        "trade_id": "test_trade",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 85000.0,
        "exit_price": 85200.0,
        "stop_loss": 84900.0,
        "take_profit": 85200.0,
        "outcome": "WIN",
        "realized_pnl": 2.0,
        "exit_reason": "TAKE_PROFIT_HIT",
    }
    fig = create_trade_replay_figure(trade)
    assert fig is not None
    # Figure should contain candlestick trace and markers
    assert len(fig.data) >= 3
