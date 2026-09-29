"""Pure Decimal mathematical performance metrics for backtesting."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, getcontext

from trad_auto.backtest.models import EquityPoint, PerformanceMetrics, TradeRecord
from trad_auto.core.constants import ZERO_DECIMAL

# Ensure high precision for Decimal square roots and exponentiation
getcontext().prec = 36


def _decimal_sqrt(value: Decimal) -> Decimal:
    """Safe Decimal square root, returning ZERO_DECIMAL for non-positive values."""
    if value <= ZERO_DECIMAL:
        return ZERO_DECIMAL
    return value.sqrt()


def calculate_drawdown_series(
    equity_curve: Sequence[EquityPoint],
) -> tuple[Decimal, Decimal]:
    """Calculates peak-to-trough maximum drawdown in currency and percentage.

    Returns:
        tuple[max_drawdown, max_drawdown_pct]
    """
    if not equity_curve:
        return ZERO_DECIMAL, ZERO_DECIMAL

    peak = equity_curve[0].total_equity
    max_dd = ZERO_DECIMAL
    max_dd_pct = ZERO_DECIMAL

    for pt in equity_curve:
        if pt.total_equity > peak:
            peak = pt.total_equity

        dd = peak - pt.total_equity
        if peak > ZERO_DECIMAL:
            dd_pct = (dd / peak) * Decimal("100.0")
        else:
            dd_pct = ZERO_DECIMAL

        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return max_dd, max_dd_pct


def calculate_consecutive_streaks(trades: Sequence[TradeRecord]) -> tuple[int, int]:
    """Calculates maximum consecutive winning and losing trade streaks.

    Returns:
        tuple[max_consecutive_wins, max_consecutive_losses]
    """
    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0

    for trade in trades:
        if trade.realized_pnl > ZERO_DECIMAL:
            current_wins += 1
            current_losses = 0
            if current_wins > max_wins:
                max_wins = current_wins
        elif trade.realized_pnl < ZERO_DECIMAL:
            current_losses += 1
            current_wins = 0
            if current_losses > max_losses:
                max_losses = current_losses
        else:
            # Breakeven resets active streaks
            current_wins = 0
            current_losses = 0

    return max_wins, max_losses


def calculate_performance_metrics(
    initial_cash: Decimal,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    start_time: datetime,
    end_time: datetime,
    risk_free_rate: Decimal = ZERO_DECIMAL,
) -> PerformanceMetrics:
    """Computes comprehensive quantitative metrics using pure Decimal arithmetic.

    Args:
        initial_cash: Starting cash allocation.
        equity_curve: Sequential portfolio equity snapshots.
        trades: List of completed round-trip trades.
        start_time: Start of simulation window.
        end_time: End of simulation window.
        risk_free_rate: Annualized risk-free benchmark rate (e.g. Decimal("0.02") for 2%).

    Returns:
        PerformanceMetrics populated with institutional analytics.
    """
    final_equity = equity_curve[-1].total_equity if equity_curve else initial_cash

    # 1. Total Return & Annualized CAGR
    if initial_cash > ZERO_DECIMAL:
        total_return_pct = ((final_equity - initial_cash) / initial_cash) * Decimal("100.0")
    else:
        total_return_pct = ZERO_DECIMAL

    duration_seconds = Decimal(str(max((end_time - start_time).total_seconds(), 1.0)))
    duration_years = duration_seconds / (Decimal("365.25") * Decimal("86400"))

    is_valid_period = (
        duration_years > ZERO_DECIMAL
        and final_equity > ZERO_DECIMAL
        and initial_cash > ZERO_DECIMAL
    )
    if is_valid_period:
        # CAGR = (final / initial) ** (1 / years) - 1
        equity_ratio = final_equity / initial_cash
        exponent = Decimal("1.0") / duration_years
        cagr_pct = ((equity_ratio**exponent) - Decimal("1.0")) * Decimal("100.0")
    else:
        cagr_pct = total_return_pct

    # 2. Maximum Drawdown
    max_dd, max_dd_pct = calculate_drawdown_series(equity_curve)

    # 3. Period Return Series for Sharpe / Sortino
    returns: list[Decimal] = []
    for i in range(1, len(equity_curve)):
        prev_eq = equity_curve[i - 1].total_equity
        curr_eq = equity_curve[i].total_equity
        if prev_eq > ZERO_DECIMAL:
            returns.append((curr_eq - prev_eq) / prev_eq)

    n_returns = len(returns)
    sharpe_ratio = ZERO_DECIMAL
    sortino_ratio = ZERO_DECIMAL

    if n_returns >= 2 and duration_years > ZERO_DECIMAL:
        periods_per_year = Decimal(str(n_returns)) / duration_years
        annualization_factor = _decimal_sqrt(periods_per_year)

        mean_return = sum(returns, ZERO_DECIMAL) / Decimal(str(n_returns))

        # Sample variance: sum((r - mean)^2) / (n - 1)
        variance = sum(((r - mean_return) ** 2 for r in returns), ZERO_DECIMAL) / Decimal(
            str(n_returns - 1)
        )
        std_dev = _decimal_sqrt(variance)

        # Downside variance: sum(min(r - rf_per_period, 0)^2) / (n - 1)
        rf_per_period = (
            risk_free_rate / periods_per_year if periods_per_year > ZERO_DECIMAL else ZERO_DECIMAL
        )
        downside_variance = sum(
            (min(r - rf_per_period, ZERO_DECIMAL) ** 2 for r in returns), ZERO_DECIMAL
        ) / Decimal(str(n_returns - 1))
        downside_std_dev = _decimal_sqrt(downside_variance)

        annualized_excess_return = (mean_return * periods_per_year) - risk_free_rate

        if std_dev > ZERO_DECIMAL:
            sharpe_ratio = annualized_excess_return / (std_dev * annualization_factor)

        if downside_std_dev > ZERO_DECIMAL:
            sortino_ratio = annualized_excess_return / (downside_std_dev * annualization_factor)

    # 4. Calmar Ratio: CAGR / Max Drawdown %
    if max_dd_pct > ZERO_DECIMAL:
        calmar_ratio = cagr_pct / max_dd_pct
    else:
        calmar_ratio = ZERO_DECIMAL

    # 5. Trade Analytics
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.realized_pnl > ZERO_DECIMAL)
    losing_trades = sum(1 for t in trades if t.realized_pnl < ZERO_DECIMAL)
    breakeven_trades = sum(1 for t in trades if t.realized_pnl == ZERO_DECIMAL)

    win_rate_pct = (
        (Decimal(str(winning_trades)) / Decimal(str(total_trades))) * Decimal("100.0")
        if total_trades > 0
        else ZERO_DECIMAL
    )

    gross_profit = sum(
        (t.realized_pnl for t in trades if t.realized_pnl > ZERO_DECIMAL),
        ZERO_DECIMAL,
    )
    gross_loss = abs(
        sum(
            (t.realized_pnl for t in trades if t.realized_pnl < ZERO_DECIMAL),
            ZERO_DECIMAL,
        )
    )

    if gross_loss > ZERO_DECIMAL:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > ZERO_DECIMAL:
        profit_factor = Decimal("999.99")
    else:
        profit_factor = ZERO_DECIMAL

    if total_trades > 0:
        avg_trade_pnl = sum((t.realized_pnl for t in trades), ZERO_DECIMAL) / Decimal(
            str(total_trades)
        )
        avg_trade_pnl_pct = sum((t.pnl_pct for t in trades), ZERO_DECIMAL) / Decimal(
            str(total_trades)
        )
        avg_holding_duration_seconds = sum(
            (t.holding_duration_seconds for t in trades), ZERO_DECIMAL
        ) / Decimal(str(total_trades))
    else:
        avg_trade_pnl = ZERO_DECIMAL
        avg_trade_pnl_pct = ZERO_DECIMAL
        avg_holding_duration_seconds = ZERO_DECIMAL

    wins = [t.realized_pnl for t in trades if t.realized_pnl > ZERO_DECIMAL]
    losses = [t.realized_pnl for t in trades if t.realized_pnl < ZERO_DECIMAL]

    largest_win = max(wins) if wins else ZERO_DECIMAL
    largest_loss = min(losses) if losses else ZERO_DECIMAL

    max_wins, max_losses = calculate_consecutive_streaks(trades)

    return PerformanceMetrics(
        total_return_pct=total_return_pct,
        cagr_pct=cagr_pct,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=breakeven_trades,
        avg_trade_pnl=avg_trade_pnl,
        avg_trade_pnl_pct=avg_trade_pnl_pct,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
        avg_holding_duration_seconds=avg_holding_duration_seconds,
    )
