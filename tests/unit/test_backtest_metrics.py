"""Unit tests for backtest quantitative performance metrics."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trad_auto.backtest.metrics import (
    calculate_consecutive_streaks,
    calculate_drawdown_series,
    calculate_performance_metrics,
)
from trad_auto.backtest.models import EquityPoint, TradeRecord
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionSide


def _make_equity_point(dt: datetime, equity: Decimal) -> EquityPoint:
    return EquityPoint(
        timestamp=dt,
        cash=equity,
        unrealized_pnl=ZERO_DECIMAL,
        realized_pnl=ZERO_DECIMAL,
        total_equity=equity,
        drawdown=ZERO_DECIMAL,
        drawdown_pct=ZERO_DECIMAL,
    )


def _make_trade(pnl: Decimal, duration_sec: Decimal = Decimal("3600")) -> TradeRecord:
    t0 = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=int(duration_sec))
    return TradeRecord(
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000") if pnl >= 0 else Decimal("49000"),
        quantity=Decimal("1.0"),
        realized_pnl=pnl,
        pnl_pct=Decimal("2.0") if pnl >= 0 else Decimal("-2.0"),
        fees=Decimal("1.0"),
        entry_time=t0,
        exit_time=t1,
        holding_duration_seconds=duration_sec,
    )


class TestDrawdownSeries:
    """Peak-to-trough drawdown calculation tests."""

    def test_empty_series_returns_zero(self) -> None:
        dd, dd_pct = calculate_drawdown_series([])
        assert dd == ZERO_DECIMAL
        assert dd_pct == ZERO_DECIMAL

    def test_monotonically_increasing_equity_has_zero_drawdown(self) -> None:
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        pts = [
            _make_equity_point(t0 + timedelta(hours=i), Decimal(str(10000 + i * 100)))
            for i in range(5)
        ]
        dd, dd_pct = calculate_drawdown_series(pts)
        assert dd == ZERO_DECIMAL
        assert dd_pct == ZERO_DECIMAL

    def test_declining_equity_drawdown(self) -> None:
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        pts = [
            _make_equity_point(t0, Decimal("10000")),
            _make_equity_point(t0 + timedelta(hours=1), Decimal("9000")),
            _make_equity_point(t0 + timedelta(hours=2), Decimal("8000")),
        ]
        dd, dd_pct = calculate_drawdown_series(pts)
        assert dd == Decimal("2000")
        assert dd_pct == Decimal("20.0")

    def test_peak_trough_peak_drawdown(self) -> None:
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        pts = [
            _make_equity_point(t0, Decimal("10000")),
            _make_equity_point(t0 + timedelta(hours=1), Decimal("12000")),  # New peak
            _make_equity_point(
                t0 + timedelta(hours=2), Decimal("9000")
            ),  # Drawdown 3000 from 12000 (25%)
            _make_equity_point(t0 + timedelta(hours=3), Decimal("11000")),  # Recovering
        ]
        dd, dd_pct = calculate_drawdown_series(pts)
        assert dd == Decimal("3000")
        assert dd_pct == Decimal("25.0")


class TestConsecutiveStreaks:
    """Streak tracking tests."""

    def test_empty_trades(self) -> None:
        wins, losses = calculate_consecutive_streaks([])
        assert wins == 0
        assert losses == 0

    def test_mixed_streaks(self) -> None:
        pnls = [
            Decimal("100"),
            Decimal("200"),
            Decimal("-50"),
            Decimal("100"),
            Decimal("200"),
            Decimal("300"),
            Decimal("-10"),
            Decimal("-20"),
        ]
        trades = [_make_trade(p) for p in pnls]
        wins, losses = calculate_consecutive_streaks(trades)
        assert wins == 3  # Three consecutive wins (100, 200, 300)
        assert losses == 2  # Two consecutive losses (-10, -20)

    def test_breakeven_resets_streak(self) -> None:
        pnls = [Decimal("100"), Decimal("0"), Decimal("100")]
        trades = [_make_trade(p) for p in pnls]
        wins, losses = calculate_consecutive_streaks(trades)
        assert wins == 1
        assert losses == 0


class TestCalculatePerformanceMetrics:
    """Comprehensive metric evaluation tests."""

    def test_zero_trades_empty_equity(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 31, tzinfo=UTC)
        m = calculate_performance_metrics(
            initial_cash=Decimal("10000"),
            equity_curve=[],
            trades=[],
            start_time=start,
            end_time=end,
        )
        assert m.total_return_pct == ZERO_DECIMAL
        assert m.total_trades == 0
        assert m.win_rate_pct == ZERO_DECIMAL
        assert m.profit_factor == ZERO_DECIMAL
        assert m.sharpe_ratio == ZERO_DECIMAL

    def test_all_winning_trades_profit_factor_capped(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 31, tzinfo=UTC)
        trades = [_make_trade(Decimal("100")), _make_trade(Decimal("200"))]
        pts = [
            _make_equity_point(start, Decimal("10000")),
            _make_equity_point(end, Decimal("10300")),
        ]
        m = calculate_performance_metrics(
            initial_cash=Decimal("10000"),
            equity_curve=pts,
            trades=trades,
            start_time=start,
            end_time=end,
        )
        assert m.total_trades == 2
        assert m.winning_trades == 2
        assert m.losing_trades == 0
        assert m.win_rate_pct == Decimal("100.0")
        assert m.profit_factor == Decimal("999.99")
        assert m.total_return_pct == Decimal("3.0")

    def test_mixed_trades_statistics(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 2, 1, tzinfo=UTC)
        trades = [
            _make_trade(Decimal("300"), duration_sec=Decimal("7200")),
            _make_trade(Decimal("300"), duration_sec=Decimal("7200")),
            _make_trade(Decimal("-100"), duration_sec=Decimal("3600")),
            _make_trade(Decimal("-100"), duration_sec=Decimal("3600")),
        ]
        pts = [
            _make_equity_point(start, Decimal("10000")),
            _make_equity_point(start + timedelta(days=10), Decimal("10300")),
            _make_equity_point(start + timedelta(days=20), Decimal("10600")),
            _make_equity_point(start + timedelta(days=25), Decimal("10500")),
            _make_equity_point(end, Decimal("10400")),
        ]
        m = calculate_performance_metrics(
            initial_cash=Decimal("10000"),
            equity_curve=pts,
            trades=trades,
            start_time=start,
            end_time=end,
        )
        assert m.total_trades == 4
        assert m.winning_trades == 2
        assert m.losing_trades == 2
        assert m.win_rate_pct == Decimal("50.0")
        # Gross profit = 600, Gross loss = 200 -> Profit Factor = 3.0
        assert m.profit_factor == Decimal("3.0")
        assert m.largest_win == Decimal("300")
        assert m.largest_loss == Decimal("-100")
        assert m.avg_trade_pnl == Decimal("100")
        # Avg holding duration: (7200 + 7200 + 3600 + 3600) / 4 = 5400s
        assert m.avg_holding_duration_seconds == Decimal("5400")
        assert m.sharpe_ratio > ZERO_DECIMAL
        assert m.sortino_ratio > ZERO_DECIMAL
