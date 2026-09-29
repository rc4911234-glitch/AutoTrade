"""Unit tests for backtesting models and data structures."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trad_auto.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PerformanceMetrics,
    TradeRecord,
)
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionSide
from trad_auto.core.exceptions import DomainValidationError


def _make_valid_config() -> BacktestConfig:
    start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    end = datetime(2025, 1, 31, 0, 0, tzinfo=UTC)
    return BacktestConfig(
        symbol="BTCUSDT",
        start_time=start,
        end_time=end,
        initial_cash=Decimal("10000.00"),
    )


class TestBacktestConfigValidation:
    """Validation invariants for BacktestConfig."""

    def test_valid_config_instantiates(self) -> None:
        cfg = _make_valid_config()
        assert cfg.symbol == "BTCUSDT"
        assert cfg.initial_cash == Decimal("10000.00")
        assert cfg.timeframe == "1h"

    def test_empty_symbol_raises(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        with pytest.raises(DomainValidationError, match="symbol cannot be empty"):
            BacktestConfig(symbol="", start_time=start, end_time=end)

    def test_naive_datetime_raises(self) -> None:
        naive_start = datetime(2025, 1, 1)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            BacktestConfig(symbol="BTCUSDT", start_time=naive_start, end_time=end)

    def test_end_time_not_after_start_time_raises(self) -> None:
        start = datetime(2025, 1, 2, tzinfo=UTC)
        end = datetime(2025, 1, 1, tzinfo=UTC)
        with pytest.raises(DomainValidationError, match="strictly after start_time"):
            BacktestConfig(symbol="BTCUSDT", start_time=start, end_time=end)

        with pytest.raises(DomainValidationError, match="strictly after start_time"):
            BacktestConfig(symbol="BTCUSDT", start_time=start, end_time=start)

    def test_non_positive_initial_cash_raises(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        with pytest.raises(DomainValidationError, match="strictly positive"):
            BacktestConfig(
                symbol="BTCUSDT",
                start_time=start,
                end_time=end,
                initial_cash=ZERO_DECIMAL,
            )

    def test_negative_rates_raise(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        with pytest.raises(DomainValidationError, match="maker_fee_rate"):
            BacktestConfig(
                symbol="BTCUSDT",
                start_time=start,
                end_time=end,
                maker_fee_rate=Decimal("-0.001"),
            )
        with pytest.raises(DomainValidationError, match="taker_fee_rate"):
            BacktestConfig(
                symbol="BTCUSDT",
                start_time=start,
                end_time=end,
                taker_fee_rate=Decimal("-0.001"),
            )
        with pytest.raises(DomainValidationError, match="slippage_rate"):
            BacktestConfig(
                symbol="BTCUSDT",
                start_time=start,
                end_time=end,
                slippage_rate=Decimal("-0.001"),
            )


class TestEquityPointValidation:
    """Validation invariants for EquityPoint."""

    def test_naive_timestamp_raises(self) -> None:
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            EquityPoint(
                timestamp=datetime(2025, 1, 1),
                cash=Decimal("10000"),
                unrealized_pnl=ZERO_DECIMAL,
                realized_pnl=ZERO_DECIMAL,
                total_equity=Decimal("10000"),
                drawdown=ZERO_DECIMAL,
                drawdown_pct=ZERO_DECIMAL,
            )


class TestTradeRecordValidation:
    """Validation invariants for TradeRecord."""

    def test_naive_timestamp_raises(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(DomainValidationError, match="timezone-aware"):
            TradeRecord(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                entry_price=Decimal("50000"),
                exit_price=Decimal("52000"),
                quantity=Decimal("0.1"),
                realized_pnl=Decimal("200"),
                pnl_pct=Decimal("4.0"),
                fees=Decimal("2.0"),
                entry_time=datetime(2025, 1, 1),
                exit_time=now,
                holding_duration_seconds=Decimal("3600"),
            )

    def test_non_positive_quantity_raises(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(DomainValidationError, match="quantity must be strictly positive"):
            TradeRecord(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                entry_price=Decimal("50000"),
                exit_price=Decimal("52000"),
                quantity=ZERO_DECIMAL,
                realized_pnl=Decimal("200"),
                pnl_pct=Decimal("4.0"),
                fees=Decimal("2.0"),
                entry_time=now,
                exit_time=now + timedelta(hours=1),
                holding_duration_seconds=Decimal("3600"),
            )


class TestBacktestResultSummary:
    """Report formatting tests."""

    def test_formatted_summary_structure(self) -> None:
        cfg = _make_valid_config()
        metrics = PerformanceMetrics(
            total_return_pct=Decimal("15.50"),
            cagr_pct=Decimal("25.30"),
            max_drawdown=Decimal("500.00"),
            max_drawdown_pct=Decimal("4.50"),
            sharpe_ratio=Decimal("1.85"),
            sortino_ratio=Decimal("2.40"),
            calmar_ratio=Decimal("5.62"),
            win_rate_pct=Decimal("60.00"),
            profit_factor=Decimal("2.10"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            breakeven_trades=0,
            avg_trade_pnl=Decimal("155.00"),
            avg_trade_pnl_pct=Decimal("1.55"),
            largest_win=Decimal("450.00"),
            largest_loss=Decimal("-200.00"),
            max_consecutive_wins=3,
            max_consecutive_losses=2,
            avg_holding_duration_seconds=Decimal("7200"),
        )
        res = BacktestResult(config=cfg, metrics=metrics)
        report = res.formatted_summary()

        assert "TRAD-AUTO BACKTEST REPORT" in report
        assert "BTCUSDT" in report
        assert "+15.50%" in report
        assert "1.85" in report
        assert "60.00%" in report
        assert "(6W / 4L / 0E)" in report
