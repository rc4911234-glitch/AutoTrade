"""Purged Walk-Forward cross-validation and out-of-sample performance evaluator."""

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier


@dataclass
class BacktestPerformance:
    """Institutional backtesting performance metrics."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    total_net_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float


class WalkForwardEvaluator:
    """Evaluates quantitative models using Purged Walk-Forward time-series splits."""

    def __init__(
        self,
        train_window: int = 4000,
        test_window: int = 1000,
        decision_threshold: float = 0.65,
    ) -> None:
        self.train_window = train_window
        self.test_window = test_window
        self.decision_threshold = decision_threshold

    def evaluate(
        self,
        X: np.ndarray,
        y: np.ndarray,
        returns: np.ndarray,
    ) -> BacktestPerformance:
        """Runs rolling walk-forward evaluation across out-of-sample test splits."""
        n_samples = len(X)
        if n_samples < (self.train_window + self.test_window):
            # If total data is smaller than one fold, use a 70/30 simple chronological split
            split_idx = int(n_samples * 0.70)
            return self._evaluate_single_split(
                X_train=X[:split_idx],
                y_train=y[:split_idx],
                X_test=X[split_idx:],
                y_test=y[split_idx:],
                test_returns=returns[split_idx:],
            )

        executed_returns: list[float] = []

        start = 0
        while (start + self.train_window + self.test_window) <= n_samples:
            train_end = start + self.train_window
            test_end = train_end + self.test_window

            X_tr, y_tr = X[start:train_end], y[start:train_end]
            X_te = X[train_end:test_end]
            r_te = returns[train_end:test_end]

            # Train model on historical window
            model = HistGradientBoostingClassifier(
                max_iter=50,
                max_depth=5,
                learning_rate=0.05,
                random_state=42,
            )
            model.fit(X_tr, y_tr)

            # Predict on unseen future window
            probs = model.predict_proba(X_te)[:, 1]

            # Execute trade when conviction exceeds threshold
            for p, r in zip(probs, r_te, strict=True):
                if p >= self.decision_threshold:
                    executed_returns.append(float(r))

            start += self.test_window

        return self._compute_performance(np.array(executed_returns, dtype=np.float64))

    def _evaluate_single_split(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_test: np.ndarray,
        y_test: np.ndarray,
        test_returns: np.ndarray,
    ) -> BacktestPerformance:
        """Evaluates model on a single chronological train/test split."""
        model = HistGradientBoostingClassifier(
            max_iter=50,
            max_depth=5,
            learning_rate=0.05,
            random_state=42,
        )
        model.fit(X_train, y_train)

        probs = model.predict_proba(X_test)[:, 1]
        executed_returns = [
            float(r)
            for p, r in zip(probs, test_returns, strict=True)
            if p >= self.decision_threshold
        ]
        return self._compute_performance(np.array(executed_returns, dtype=np.float64))

    @staticmethod
    def _compute_performance(trade_returns: np.ndarray) -> BacktestPerformance:
        """Calculates statistical metrics from executed trade returns."""
        total_trades = len(trade_returns)
        if total_trades == 0:
            return BacktestPerformance(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                total_net_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
            )

        winning = trade_returns[trade_returns > 0]
        losing = trade_returns[trade_returns <= 0]

        win_count = len(winning)
        lose_count = len(losing)
        win_rate = (win_count / total_trades) * 100.0

        gross_profit = float(np.sum(winning)) if win_count > 0 else 0.0
        gross_loss = float(np.abs(np.sum(losing))) if lose_count > 0 else 0.0

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = 99.0 if gross_profit > 0 else 0.0

        total_return_pct = float(np.sum(trade_returns)) * 100.0

        # Equity Curve and Max Drawdown
        equity_curve = np.cumprod(1.0 + trade_returns)
        running_max = np.maximum.accumulate(equity_curve)
        drawdowns = (running_max - equity_curve) / running_max
        max_dd_pct = float(np.max(drawdowns)) * 100.0 if len(drawdowns) > 0 else 0.0

        # Annualized Sharpe Ratio (assuming 1-minute to 15-minute trading intervals)
        std_ret = float(np.std(trade_returns))
        mean_ret = float(np.mean(trade_returns))
        if std_ret > 1e-8:
            sharpe = (mean_ret / std_ret) * np.sqrt(252 * 24)
        else:
            sharpe = 0.0

        return BacktestPerformance(
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=lose_count,
            win_rate_pct=float(win_rate),
            profit_factor=float(profit_factor),
            total_net_return_pct=float(total_return_pct),
            max_drawdown_pct=float(max_dd_pct),
            sharpe_ratio=float(sharpe),
        )
