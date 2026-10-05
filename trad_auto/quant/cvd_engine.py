"""Institutional Cumulative Volume Delta (CVD) & Delta Divergence Engine.

Detects institutional absorption, exhaustion traps, and true market participant aggression:
1. Bar Delta: (Aggressive Taker Market Buys) - (Aggressive Taker Market Sells).
2. Cumulative Volume Delta (CVD): Running cumulative sum of buyer vs seller pressure.
3. Delta Ratio: Percentage of total volume driven by aggressive taker buyers (>50% = Net Buyer Aggression).
4. Delta Z-Score: Statistical standard deviations of current delta vs rolling history.
5. Divergence Signals:
   - BULLISH_ABSORPTION: Price declining or flat while CVD is surging upwards (Passive limit buyers absorbing aggressive sellers -> High probability bullish reversal).
   - BEARISH_EXHAUSTION: Price making higher highs while CVD is declining downwards (Smart money distribution / Bull trap -> High probability bearish rejection).
   - BULLISH_CONFIRMATION: Price rising with CVD expansion.
   - BEARISH_CONFIRMATION: Price falling with CVD contraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np


@dataclass
class CVDAnalysisResult:
    """Quantitative CVD and Order Flow Divergence snapshot."""

    symbol: str
    current_cvd: float
    cvd_change_window: float
    delta_ratio: float  # e.g. 0.54 = 54% Taker Buy Volume
    delta_zscore: float  # Z-score of current bar delta vs lookback
    divergence_signal: str  # 'BULLISH_ABSORPTION' | 'BEARISH_EXHAUSTION' | 'BULLISH_CONFIRMATION' | 'BEARISH_CONFIRMATION' | 'NEUTRAL'
    aggressor_side: str  # 'BUYERS' | 'SELLERS' | 'BALANCED'
    bars_analyzed: int
    allow_long: bool
    allow_short: bool
    summary: str


class CumulativeVolumeDeltaEngine:
    """Institutional Order Flow Delta & Divergence Engine.
    
    Zero future-leak, vectorized computation across OHLCV bars.
    """

    def __init__(
        self,
        lookback_window: int = 20,
        divergence_threshold_pct: float = 0.20,  # 0.20% price move threshold
    ) -> None:
        self.lookback_window = max(5, lookback_window)
        self.divergence_threshold_pct = divergence_threshold_pct

    def compute_cvd(
        self,
        candles: Any,
        symbol: str = "BTCUSDT",
    ) -> CVDAnalysisResult:
        """Analyzes CVD and order flow divergence from OHLCV bars."""
        closes, volumes, taker_buys = self._extract_data(candles)
        n = len(closes)

        if n < 3:
            return CVDAnalysisResult(
                symbol=symbol,
                current_cvd=0.0,
                cvd_change_window=0.0,
                delta_ratio=0.50,
                delta_zscore=0.0,
                divergence_signal="NEUTRAL",
                aggressor_side="BALANCED",
                bars_analyzed=n,
                allow_long=True,
                allow_short=True,
                summary="Insufficient bars for CVD analysis",
            )

        # 1. Compute Individual Bar Deltas: (Taker Buy) - (Taker Sell)
        # Since Taker Sell = Volume - Taker Buy: Delta = 2 * Taker Buy - Volume
        bar_deltas = (2.0 * taker_buys) - volumes

        # 2. Cumulative Volume Delta (CVD)
        cvd_series = np.cumsum(bar_deltas)
        current_cvd = float(cvd_series[-1])

        # 3. Window Lookback Analysis
        w = min(self.lookback_window, n)
        window_deltas = bar_deltas[-w:]
        window_vols = volumes[-w:]
        window_buys = taker_buys[-w:]

        tot_vol_w = float(np.sum(window_vols))
        tot_buys_w = float(np.sum(window_buys))
        delta_ratio = round(tot_buys_w / tot_vol_w, 3) if tot_vol_w > 0 else 0.50

        # Current Delta Z-Score
        std_delta = float(np.std(window_deltas))
        mean_delta = float(np.mean(window_deltas))
        if std_delta > 1e-8:
            delta_zscore = round((bar_deltas[-1] - mean_delta) / std_delta, 2)
        else:
            delta_zscore = 0.0

        # 4. CVD Change across window
        cvd_start = float(cvd_series[-w])
        cvd_change = current_cvd - cvd_start

        # Price Change across window
        price_start = float(closes[-w])
        price_end = float(closes[-1])
        price_change_pct = ((price_end - price_start) / (price_start + 1e-8)) * 100.0

        # 5. Order Flow Divergence Classification
        # Price DOWN or FLAT but CVD UP => Bullish Absorption
        if price_change_pct <= -self.divergence_threshold_pct and cvd_change > 0:
            divergence_signal = "BULLISH_ABSORPTION"
            aggressor_side = "BUYERS"
            allow_long = True
            allow_short = False
            summary = (
                f"Bullish Absorption detected: Price down {price_change_pct:.2f}% but CVD rising "
                f"(+{cvd_change:,.1f}). Strong passive buyer absorption."
            )
        # Price UP or FLAT but CVD DOWN => Bearish Exhaustion / Trap
        elif price_change_pct >= self.divergence_threshold_pct and cvd_change < 0:
            divergence_signal = "BEARISH_EXHAUSTION"
            aggressor_side = "SELLERS"
            allow_long = False
            allow_short = True
            summary = (
                f"Bearish Exhaustion detected: Price up +{price_change_pct:.2f}% but CVD falling "
                f"({cvd_change:,.1f}). Lack of aggressive buy backing (Bull Trap)."
            )
        # Price UP and CVD UP => Bullish Trend Confirmation
        elif price_change_pct >= self.divergence_threshold_pct and cvd_change > 0:
            divergence_signal = "BULLISH_CONFIRMATION"
            aggressor_side = "BUYERS"
            allow_long = True
            allow_short = True
            summary = (
                f"Bullish Trend Confirmed: Price (+{price_change_pct:.2f}%) and CVD (+{cvd_change:,.1f}) "
                f"expanding in mutual harmony."
            )
        # Price DOWN and CVD DOWN => Bearish Trend Confirmation
        elif price_change_pct <= -self.divergence_threshold_pct and cvd_change < 0:
            divergence_signal = "BEARISH_CONFIRMATION"
            aggressor_side = "SELLERS"
            allow_long = True
            allow_short = True
            summary = (
                f"Bearish Trend Confirmed: Price ({price_change_pct:.2f}%) and CVD ({cvd_change:,.1f}) "
                f"contracting in mutual harmony."
            )
        else:
            divergence_signal = "NEUTRAL"
            aggressor_side = "BALANCED" if 0.47 <= delta_ratio <= 0.53 else ("BUYERS" if delta_ratio > 0.53 else "SELLERS")
            allow_long = True
            allow_short = True
            summary = f"Balanced order flow: Delta ratio {delta_ratio*100:.1f}%, CVD change {cvd_change:,.1f}."

        return CVDAnalysisResult(
            symbol=symbol,
            current_cvd=round(current_cvd, 2),
            cvd_change_window=round(cvd_change, 2),
            delta_ratio=delta_ratio,
            delta_zscore=delta_zscore,
            divergence_signal=divergence_signal,
            aggressor_side=aggressor_side,
            bars_analyzed=w,
            allow_long=allow_long,
            allow_short=allow_short,
            summary=summary,
        )

    def _extract_data(self, candles: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extracts closes, volumes, and taker_buy_volumes."""
        if hasattr(candles, "iloc"):
            # pandas DataFrame
            c = candles["close"].to_numpy(dtype=np.float64)
            v = candles["volume"].to_numpy(dtype=np.float64)
            if "taker_buy_volume" in candles.columns:
                tb = candles["taker_buy_volume"].to_numpy(dtype=np.float64)
            elif "taker_buy_vol" in candles.columns:
                tb = candles["taker_buy_vol"].to_numpy(dtype=np.float64)
            else:
                # Approximate from body ratio
                o = candles["open"].to_numpy(dtype=np.float64)
                h = candles["high"].to_numpy(dtype=np.float64)
                l = candles["low"].to_numpy(dtype=np.float64)
                rng = np.maximum(h - l, 1e-8)
                body_ratio = np.clip((c - o) / rng, -1.0, 1.0)
                buy_fraction = 0.50 + (0.50 * body_ratio)
                tb = v * buy_fraction
            return c, v, tb

        c_list, v_list, tb_list = [], [], []
        if not candles:
            return np.array([]), np.array([]), np.array([])

        for item in candles:
            if hasattr(item, "close"):
                c_val = float(item.close)
                v_val = float(item.volume)
                tb_val = float(getattr(item, "taker_buy_volume", v_val * 0.5))
            elif isinstance(item, dict):
                c_val = float(item.get("close", 0.0))
                v_val = float(item.get("volume", 0.0))
                if "taker_buy_volume" in item:
                    tb_val = float(item["taker_buy_volume"])
                elif "open" in item and "high" in item and "low" in item:
                    o = float(item["open"])
                    h = float(item["high"])
                    l = float(item["low"])
                    rng = max(h - l, 1e-8)
                    tb_val = v_val * np.clip(0.50 + 0.50 * ((c_val - o) / rng), 0.0, 1.0)
                else:
                    tb_val = v_val * 0.50
            elif isinstance(item, (list, tuple)) and len(item) >= 6:
                c_val = float(item[4])
                v_val = float(item[5])
                tb_val = float(item[9]) if len(item) > 9 else v_val * 0.50
            else:
                continue

            c_list.append(c_val)
            v_list.append(v_val)
            tb_list.append(tb_val)

        return (
            np.array(c_list, dtype=np.float64),
            np.array(v_list, dtype=np.float64),
            np.array(tb_list, dtype=np.float64),
        )
