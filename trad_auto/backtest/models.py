"""Domain models and data structures for event-driven backtesting."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionSide
from trad_auto.core.exceptions import DomainValidationError


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration parameters for a backtest simulation run."""

    symbol: str
    start_time: datetime
    end_time: datetime
    initial_cash: Decimal = Decimal("10000.00")
    maker_fee_rate: Decimal = Decimal("0.0002")  # 0.02%
    taker_fee_rate: Decimal = Decimal("0.0005")  # 0.05%
    slippage_rate: Decimal = Decimal("0.0001")  # 0.01%
    risk_free_rate: Decimal = ZERO_DECIMAL  # Annualized risk-free rate
    timeframe: str = "1h"
    default_quote_asset: str = "USDT"

    def __post_init__(self) -> None:
        if not self.symbol:
            raise DomainValidationError("BacktestConfig symbol cannot be empty")
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise DomainValidationError("start_time and end_time must be timezone-aware (UTC)")
        if self.end_time <= self.start_time:
            raise DomainValidationError("end_time must be strictly after start_time")
        if self.initial_cash <= ZERO_DECIMAL:
            raise DomainValidationError("initial_cash must be strictly positive")
        if self.maker_fee_rate < ZERO_DECIMAL:
            raise DomainValidationError("maker_fee_rate cannot be negative")
        if self.taker_fee_rate < ZERO_DECIMAL:
            raise DomainValidationError("taker_fee_rate cannot be negative")
        if self.slippage_rate < ZERO_DECIMAL:
            raise DomainValidationError("slippage_rate cannot be negative")


@dataclass(frozen=True)
class EquityPoint:
    """Snapshot of portfolio equity at a specific timestamp."""

    timestamp: datetime
    cash: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    total_equity: Decimal
    drawdown: Decimal
    drawdown_pct: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("timestamp must be timezone-aware (UTC)")


@dataclass(frozen=True)
class TradeRecord:
    """Historical record of an executed and completed round-trip trade."""

    symbol: str
    side: PositionSide
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    realized_pnl: Decimal
    pnl_pct: Decimal
    fees: Decimal
    entry_time: datetime
    exit_time: datetime
    holding_duration_seconds: Decimal

    def __post_init__(self) -> None:
        if self.entry_time.tzinfo is None or self.exit_time.tzinfo is None:
            raise DomainValidationError("TradeRecord timestamps must be timezone-aware (UTC)")
        if self.quantity <= ZERO_DECIMAL:
            raise DomainValidationError("TradeRecord quantity must be strictly positive")


@dataclass(frozen=True)
class PerformanceMetrics:
    """Institutional performance analytics evaluated over a backtest simulation."""

    total_return_pct: Decimal
    cagr_pct: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    win_rate_pct: Decimal
    profit_factor: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    avg_trade_pnl: Decimal
    avg_trade_pnl_pct: Decimal
    largest_win: Decimal
    largest_loss: Decimal
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_holding_duration_seconds: Decimal


@dataclass(frozen=True)
class BacktestResult:
    """Complete results package from an event-driven backtest simulation."""

    config: BacktestConfig
    metrics: PerformanceMetrics
    equity_curve: list[EquityPoint] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)

    def formatted_summary(self) -> str:
        """Formats performance metrics as a structured institutional summary report."""
        m = self.metrics
        c = self.config

        start_str = c.start_time.strftime("%Y-%m-%d %H:%M")
        end_str = c.end_time.strftime("%Y-%m-%d %H:%M")
        win_loss_str = f"({m.winning_trades}W / {m.losing_trades}L / {m.breakeven_trades}E)"
        avg_dur_hours = m.avg_holding_duration_seconds / Decimal("3600")

        lines = [
            "==================================================================",
            "                   TRAD-AUTO BACKTEST REPORT                      ",
            "==================================================================",
            f" Symbol:                 {c.symbol} ({c.timeframe})",
            f" Period:                 {start_str} to {end_str} UTC",
            f" Initial Capital:        ${c.initial_cash:,.2f} {c.default_quote_asset}",
            "------------------------------------------------------------------",
            " PERFORMANCE SUMMARY",
            "------------------------------------------------------------------",
            f" Total Return:           {m.total_return_pct:+.2f}%",
            f" Annualized Return(CAGR):{m.cagr_pct:+.2f}%",
            f" Max Drawdown:           -${m.max_drawdown:,.2f} ({m.max_drawdown_pct:.2f}%)",
            f" Sharpe Ratio:           {m.sharpe_ratio:.2f}",
            f" Sortino Ratio:          {m.sortino_ratio:.2f}",
            f" Calmar Ratio:           {m.calmar_ratio:.2f}",
            "------------------------------------------------------------------",
            " TRADE STATISTICS",
            "------------------------------------------------------------------",
            f" Total Closed Trades:    {m.total_trades}",
            f" Win Rate:               {m.win_rate_pct:.2f}% {win_loss_str}",
            f" Profit Factor:          {m.profit_factor:.2f}",
            f" Average Trade P&L:      ${m.avg_trade_pnl:+,.2f} ({m.avg_trade_pnl_pct:+.2f}%)",
            f" Largest Win:            +${m.largest_win:,.2f}",
            f" Largest Loss:           -${abs(m.largest_loss):,.2f}",
            f" Max Consecutive Wins:   {m.max_consecutive_wins}",
            f" Max Consecutive Losses: {m.max_consecutive_losses}",
            f" Avg Holding Duration:   {avg_dur_hours:.1f} hours",
            "==================================================================",
        ]
        return "\n".join(lines)
