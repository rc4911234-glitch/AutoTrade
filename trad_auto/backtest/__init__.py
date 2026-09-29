"""Event-driven backtesting engine and quantitative performance analytics."""

from trad_auto.backtest.metrics import calculate_performance_metrics
from trad_auto.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    PerformanceMetrics,
    TradeRecord,
)
from trad_auto.backtest.runner import BacktestRunner

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestRunner",
    "EquityPoint",
    "PerformanceMetrics",
    "TradeRecord",
    "calculate_performance_metrics",
]
