"""Triple Barrier Method labeling engine (Marcos Lopez de Prado standard)."""

from typing import Any

import numpy as np


class TripleBarrierLabeler:
    """Labels financial time series using the Triple Barrier Method.

    Instead of simplistic fixed-time horizons (e.g., return after 5 bars),
    the Triple Barrier Method realistically simulates trade geometry:
    1. Upper Horizontal Barrier: Take Profit target (e.g., +1.20% or +1.5x ATR).
    2. Lower Horizontal Barrier: Stop Loss boundary (e.g., -0.60% or -0.75x ATR) -> 1:2 R:R.
    3. Vertical Barrier: Expiration window (e.g., max 15 bars).

    Labels:
        1: Success (Take Profit reached before Stop Loss or Expiration).
        0: Failure / Noise (Stop Loss touched first, or trade timed out in chop).
    """

    def __init__(
        self,
        take_profit_ratio: float = 0.004,  # +0.40% (40 bps scalp target)
        stop_loss_ratio: float = 0.002,  # -0.20% (20 bps stop loss -> 1:2 R:R)
        max_horizon_bars: int = 15,
        fee_cost_ratio: float = 0.0004,  # 0.04% round-trip fee slippage
    ) -> None:
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.max_horizon_bars = max_horizon_bars
        self.fee_cost_ratio = fee_cost_ratio

    def label_candles(
        self,
        candles: list[dict[str, Any]],
        valid_indices: np.ndarray,
        direction: str = "LONG",
    ) -> tuple[np.ndarray, np.ndarray]:
        """Calculates binary labels and trade returns for the valid feature rows.

        Returns:
            y: binary labels array (1 = profitable win, 0 = loss/timeout)
            returns: estimated net return array accounting for fees
        """
        n_candles = len(candles)
        n_samples = len(valid_indices)

        closes = np.array([c["close"] for c in candles], dtype=np.float64)
        highs = np.array([c["high"] for c in candles], dtype=np.float64)
        lows = np.array([c["low"] for c in candles], dtype=np.float64)

        labels = np.zeros(n_samples, dtype=np.int32)
        returns = np.zeros(n_samples, dtype=np.float64)

        for idx, candle_idx in enumerate(valid_indices):
            entry_price = closes[candle_idx]
            end_idx = min(candle_idx + self.max_horizon_bars, n_candles)

            if candle_idx >= end_idx:
                continue

            if direction.upper() == "LONG":
                tp_price = entry_price * (1.0 + self.take_profit_ratio)
                sl_price = entry_price * (1.0 - self.stop_loss_ratio)

                outcome_label = 0
                outcome_return = 0.0

                for t in range(candle_idx + 1, end_idx):
                    curr_high = highs[t]
                    curr_low = lows[t]

                    # Stop Loss hit first?
                    if curr_low <= sl_price:
                        outcome_label = 0
                        outcome_return = -self.stop_loss_ratio - self.fee_cost_ratio
                        break

                    # Take Profit hit?
                    if curr_high >= tp_price:
                        outcome_label = 1
                        outcome_return = self.take_profit_ratio - self.fee_cost_ratio
                        break

                # If neither barrier was hit, close at market on timeout
                if outcome_return == 0.0 and end_idx > candle_idx + 1:
                    exit_price = closes[end_idx - 1]
                    raw_ret = (exit_price - entry_price) / entry_price
                    outcome_return = raw_ret - self.fee_cost_ratio
                    outcome_label = 1 if outcome_return > 0 else 0

                labels[idx] = outcome_label
                returns[idx] = outcome_return

            else:  # SHORT
                tp_price = entry_price * (1.0 - self.take_profit_ratio)
                sl_price = entry_price * (1.0 + self.stop_loss_ratio)

                outcome_label = 0
                outcome_return = 0.0

                for t in range(candle_idx + 1, end_idx):
                    curr_high = highs[t]
                    curr_low = lows[t]

                    if curr_high >= sl_price:
                        outcome_label = 0
                        outcome_return = -self.stop_loss_ratio - self.fee_cost_ratio
                        break

                    if curr_low <= tp_price:
                        outcome_label = 1
                        outcome_return = self.take_profit_ratio - self.fee_cost_ratio
                        break

                if outcome_return == 0.0 and end_idx > candle_idx + 1:
                    exit_price = closes[end_idx - 1]
                    raw_ret = (entry_price - exit_price) / entry_price
                    outcome_return = raw_ret - self.fee_cost_ratio
                    outcome_label = 1 if outcome_return > 0 else 0

                labels[idx] = outcome_label
                returns[idx] = outcome_return

        return labels, returns
