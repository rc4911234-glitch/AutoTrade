"""Quantitative feature extraction engine computing tabular alpha factors."""

from typing import Any

import numpy as np


class QuantFeatureExtractor:
    """Computes mathematical alpha features from historical OHLCV data.

    Follows Microsoft Qlib & Institutional Hedge Fund standards:
    - Zero future leak: All features at index i only use candles up to i.
    - Robust normalization: Normalizes price offsets by ATR/volatility.
    - Vectorized: High-speed NumPy operations for rapid backtesting.
    """

    FEATURE_NAMES = [
        "ret_1",
        "ret_3",
        "ret_5",
        "ret_15",
        "ret_30",
        "wick_upper",
        "wick_lower",
        "body_ratio",
        "is_green",
        "vol_ratio_5",
        "vol_ratio_20",
        "taker_buy_ratio",
        "dist_ema_9",
        "dist_ema_21",
        "dist_ema_50",
        "ema_ribbon_expansion",
        "atr_ratio",
        "volatility_parkinson",
        "adx_14",
        "di_diff",
        "qlib_mom_vol",
        "qlib_vwap_div",
    ]

    def extract_features(
        self, candles: list[dict[str, Any]]
    ) -> tuple[np.ndarray, list[str], np.ndarray]:
        """Extracts tabular feature matrix X from kline dictionaries.

        Returns:
            X: np.ndarray of shape (n_samples, n_features)
            feature_names: list[str]
            valid_indices: array of candle indices where features are defined (after warmup)
        """
        n = len(candles)
        if n < 60:
            raise ValueError(f"Need at least 60 candles for warmup, got {n}")

        opens = np.array([c["open"] for c in candles], dtype=np.float64)
        highs = np.array([c["high"] for c in candles], dtype=np.float64)
        lows = np.array([c["low"] for c in candles], dtype=np.float64)
        closes = np.array([c["close"] for c in candles], dtype=np.float64)
        volumes = np.array([c["volume"] for c in candles], dtype=np.float64)
        taker_buys = np.array([c["taker_buy_volume"] for c in candles], dtype=np.float64)

        eps = 1e-8

        # 1. Returns
        ret_1 = np.zeros(n)
        ret_1[1:] = (closes[1:] - closes[:-1]) / (closes[:-1] + eps)

        ret_3 = np.zeros(n)
        ret_3[3:] = (closes[3:] - closes[:-3]) / (closes[:-3] + eps)

        ret_5 = np.zeros(n)
        ret_5[5:] = (closes[5:] - closes[:-5]) / (closes[:-5] + eps)

        ret_15 = np.zeros(n)
        ret_15[15:] = (closes[15:] - closes[:-15]) / (closes[:-15] + eps)

        ret_30 = np.zeros(n)
        ret_30[30:] = (closes[30:] - closes[:-30]) / (closes[:-30] + eps)

        # 2. Candlestick Geometry
        candle_range = highs - lows + eps
        body = np.abs(closes - opens)
        upper_wick = highs - np.maximum(opens, closes)
        lower_wick = np.minimum(opens, closes) - lows

        wick_upper = upper_wick / candle_range
        wick_lower = lower_wick / candle_range
        body_ratio = body / candle_range
        is_green = np.where(closes >= opens, 1.0, 0.0)

        # 3. Volume & Flow
        vol_sma_5 = self._rolling_mean(volumes, 5)
        vol_sma_20 = self._rolling_mean(volumes, 20)
        vol_ratio_5 = volumes / (vol_sma_5 + eps)
        vol_ratio_20 = volumes / (vol_sma_20 + eps)
        taker_buy_ratio = taker_buys / (volumes + eps)

        # 4. Moving Averages & ATR
        ema_9 = self._exponential_moving_average(closes, 9)
        ema_21 = self._exponential_moving_average(closes, 21)
        ema_50 = self._exponential_moving_average(closes, 50)

        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        tr[1:] = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        atr_14 = self._exponential_moving_average(tr, 14)
        atr_ratio = atr_14 / (closes + eps)

        dist_ema_9 = (closes - ema_9) / (atr_14 + eps)
        dist_ema_21 = (closes - ema_21) / (atr_14 + eps)
        dist_ema_50 = (closes - ema_50) / (atr_14 + eps)
        ema_ribbon_expansion = (ema_9 - ema_21) / (atr_14 + eps)

        # 5. Volatility (Parkinson)
        hl_ratio = np.log(highs / (lows + eps))
        volatility_parkinson = np.sqrt(
            self._rolling_mean(hl_ratio**2, 14) / (4.0 * np.log(2.0))
        )

        # 6. Directional Movement & ADX
        up_move = np.zeros(n)
        up_move[1:] = highs[1:] - highs[:-1]
        down_move = np.zeros(n)
        down_move[1:] = lows[:-1] - lows[1:]

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        smooth_plus_dm = self._exponential_moving_average(plus_dm, 14)
        smooth_minus_dm = self._exponential_moving_average(minus_dm, 14)

        plus_di = 100.0 * (smooth_plus_dm / (atr_14 + eps))
        minus_di = 100.0 * (smooth_minus_dm / (atr_14 + eps))

        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di + eps)
        adx_14 = self._exponential_moving_average(dx, 14)
        di_diff = (plus_di - minus_di) / (plus_di + minus_di + eps)

        # 7. Qlib Alpha Micro-factors
        ret_vol_15 = self._rolling_std(ret_1, 15)
        qlib_mom_vol = ret_5 / (ret_vol_15 + eps)

        # Rolling VWAP
        cum_pv = self._rolling_sum(closes * volumes, 15)
        cum_v = self._rolling_sum(volumes, 15)
        vwap_15 = cum_pv / (cum_v + eps)
        qlib_vwap_div = (closes - vwap_15) / (atr_14 + eps)

        # Stack into matrix
        feature_columns = [
            ret_1,
            ret_3,
            ret_5,
            ret_15,
            ret_30,
            wick_upper,
            wick_lower,
            body_ratio,
            is_green,
            vol_ratio_5,
            vol_ratio_20,
            taker_buy_ratio,
            dist_ema_9,
            dist_ema_21,
            dist_ema_50,
            ema_ribbon_expansion,
            atr_ratio,
            volatility_parkinson,
            adx_14,
            di_diff,
            qlib_mom_vol,
            qlib_vwap_div,
        ]

        matrix = np.column_stack(feature_columns)

        # Warmup cutoff: first 50 rows have incomplete EMAs
        warmup = 50
        valid_indices = np.arange(warmup, n)
        X = matrix[valid_indices]

        # Sanitize NaNs or Infs
        X = np.nan_to_num(X, nan=0.0, posinf=1.0, neginf=-1.0)
        return X, self.FEATURE_NAMES, valid_indices

    @staticmethod
    def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
        out = np.zeros_like(arr)
        if len(arr) == 0:
            return out
        cumsum = np.cumsum(np.insert(arr, 0, 0))
        out[window - 1 :] = (cumsum[window:] - cumsum[:-window]) / float(window)
        out[: window - 1] = arr[: window - 1]
        return out

    @staticmethod
    def _rolling_sum(arr: np.ndarray, window: int) -> np.ndarray:
        out = np.zeros_like(arr)
        if len(arr) == 0:
            return out
        cumsum = np.cumsum(np.insert(arr, 0, 0))
        out[window - 1 :] = cumsum[window:] - cumsum[:-window]
        out[: window - 1] = arr[: window - 1]
        return out

    @staticmethod
    def _rolling_std(arr: np.ndarray, window: int) -> np.ndarray:
        out = np.zeros_like(arr)
        for i in range(window - 1, len(arr)):
            out[i] = np.std(arr[i - window + 1 : i + 1])
        return out

    @staticmethod
    def _exponential_moving_average(arr: np.ndarray, span: int) -> np.ndarray:
        out = np.zeros_like(arr)
        if len(arr) == 0:
            return out
        alpha = 2.0 / (span + 1.0)
        out[0] = arr[0]
        for i in range(1, len(arr)):
            out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
        return out
