"""Institutional Volume Profile & Value Area Engine (Auction Market Theory).

Computes horizontal volume distribution across price bins to determine:
1. POC (Point of Control): Price bin where the highest aggregate volume traded.
2. Value Area (VAH & VAL): The price range encompassing 70% of total traded volume.
3. HVN (High Volume Nodes): Acceptance clusters (strong support & resistance magnets).
4. LVN (Low Volume Nodes): Rejection zones (liquidity voids / fast slip zones).
5. Value Area State: ABOVE_VAH (premium/breakout), INSIDE_VALUE (range/balance), BELOW_VAL (discount/breakdown).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
import numpy as np


@dataclass
class VolumeProfileResult:
    """Quantitative Volume Profile snapshot."""

    symbol: str
    poc_price: float
    vah_price: float
    val_price: float
    current_price: float
    value_area_relation: str  # 'ABOVE_VAH' | 'INSIDE_VALUE' | 'BELOW_VAL'
    distance_to_poc_pct: float  # e.g. +0.45% above POC, -0.80% below POC
    hvn_levels: list[float]  # High Volume Nodes (support/resistance)
    lvn_levels: list[float]  # Low Volume Nodes (slippage/fast zones)
    total_volume: float
    price_bins: list[float] = field(default_factory=list)
    volume_bins: list[float] = field(default_factory=list)
    summary: str = ""


class VolumeProfileEngine:
    """Institutional Auction Market Volume Profile Engine.
    
    Zero future-leak, vectorized price-level volume distribution.
    Compatible with list[Bar], list[dict], or pandas DataFrame.
    """

    def __init__(
        self,
        n_bins: int = 50,
        value_area_pct: float = 0.70,
    ) -> None:
        self.n_bins = max(10, n_bins)
        self.value_area_pct = min(0.95, max(0.50, value_area_pct))

    def compute_profile(
        self,
        candles: Any,
        symbol: str = "BTCUSDT",
    ) -> VolumeProfileResult:
        """Computes volume profile from OHLCV bars or dicts."""
        highs, lows, closes, volumes = self._extract_arrays(candles)

        if len(closes) == 0:
            return VolumeProfileResult(
                symbol=symbol,
                poc_price=0.0,
                vah_price=0.0,
                val_price=0.0,
                current_price=0.0,
                value_area_relation="INSIDE_VALUE",
                distance_to_poc_pct=0.0,
                hvn_levels=[],
                lvn_levels=[],
                total_volume=0.0,
                summary="Empty candle history",
            )

        current_price = float(closes[-1])
        min_price = float(np.min(lows))
        max_price = float(np.max(highs))
        total_vol = float(np.sum(volumes))

        if max_price <= min_price or total_vol <= 0:
            return VolumeProfileResult(
                symbol=symbol,
                poc_price=current_price,
                vah_price=current_price * 1.01,
                val_price=current_price * 0.99,
                current_price=current_price,
                value_area_relation="INSIDE_VALUE",
                distance_to_poc_pct=0.0,
                hvn_levels=[current_price],
                lvn_levels=[],
                total_volume=total_vol,
                summary="Flat market range",
            )

        # 1. Define Price Bins
        bin_edges = np.linspace(min_price, max_price, self.n_bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
        vol_distribution = np.zeros(self.n_bins, dtype=np.float64)

        # 2. Vectorized / Binned Volume Allocation across candle height
        for h, l, v in zip(highs, lows, volumes):
            if v <= 0:
                continue
            candle_h = float(h)
            candle_l = float(l)
            candle_v = float(v)

            if candle_h == candle_l:
                # Flat candle: locate single bin
                idx = int(np.clip(np.searchsorted(bin_edges, candle_h) - 1, 0, self.n_bins - 1))
                vol_distribution[idx] += candle_v
            else:
                # Distribute volume uniformly across covered bins
                low_idx = int(np.clip(np.searchsorted(bin_edges, candle_l) - 1, 0, self.n_bins - 1))
                high_idx = int(np.clip(np.searchsorted(bin_edges, candle_h) - 1, 0, self.n_bins - 1))
                num_covered = max(1, high_idx - low_idx + 1)
                vol_per_bin = candle_v / num_covered
                vol_distribution[low_idx : high_idx + 1] += vol_per_bin

        # 3. Point of Control (POC)
        poc_idx = int(np.argmax(vol_distribution))
        poc_price = float(bin_centers[poc_idx])

        # 4. Value Area Calculation (70% total volume around POC)
        target_vol = total_vol * self.value_area_pct
        accum_vol = vol_distribution[poc_idx]
        up_idx = poc_idx
        down_idx = poc_idx

        while accum_vol < target_vol and (up_idx < self.n_bins - 1 or down_idx > 0):
            next_up_vol = vol_distribution[up_idx + 1] if up_idx < self.n_bins - 1 else 0.0
            next_down_vol = vol_distribution[down_idx - 1] if down_idx > 0 else 0.0

            if next_up_vol >= next_down_vol and up_idx < self.n_bins - 1:
                up_idx += 1
                accum_vol += next_up_vol
            elif down_idx > 0:
                down_idx -= 1
                accum_vol += next_down_vol
            else:
                up_idx += 1
                accum_vol += next_up_vol

        vah_price = float(bin_edges[up_idx + 1])
        val_price = float(bin_edges[down_idx])

        # 5. High Volume Nodes (HVN) and Low Volume Nodes (LVN)
        mean_bin_vol = float(np.mean(vol_distribution))
        hvn_candidates: list[tuple[float, float]] = []
        lvn_candidates: list[float] = []

        for i in range(1, self.n_bins - 1):
            # Local peak above average
            if vol_distribution[i] > vol_distribution[i - 1] and vol_distribution[i] > vol_distribution[i + 1]:
                if vol_distribution[i] > mean_bin_vol * 1.15 and i != poc_idx:
                    hvn_candidates.append((vol_distribution[i], float(bin_centers[i])))
            # Local trough below average
            elif vol_distribution[i] < vol_distribution[i - 1] and vol_distribution[i] < vol_distribution[i + 1]:
                if vol_distribution[i] < mean_bin_vol * 0.70:
                    lvn_candidates.append(float(bin_centers[i]))

        # Top 3 HVNs sorted by volume descending
        hvn_candidates.sort(key=lambda x: x[0], reverse=True)
        hvn_levels = [lvl for _, lvl in hvn_candidates[:3]]
        lvn_levels = lvn_candidates[:3]

        # 6. Current Price Relation to Value Area
        if current_price > vah_price:
            relation = "ABOVE_VAH"
        elif current_price < val_price:
            relation = "BELOW_VAL"
        else:
            relation = "INSIDE_VALUE"

        dist_to_poc = round(((current_price - poc_price) / poc_price) * 100.0, 2)

        summary = (
            f"POC ${poc_price:,.2f} | VAH ${vah_price:,.2f} | VAL ${val_price:,.2f} | "
            f"Status: {relation} ({dist_to_poc:+.2f}% vs POC)"
        )

        return VolumeProfileResult(
            symbol=symbol,
            poc_price=round(poc_price, 2),
            vah_price=round(vah_price, 2),
            val_price=round(val_price, 2),
            current_price=round(current_price, 2),
            value_area_relation=relation,
            distance_to_poc_pct=dist_to_poc,
            hvn_levels=[round(h, 2) for h in hvn_levels],
            lvn_levels=[round(l, 2) for l in lvn_levels],
            total_volume=round(total_vol, 2),
            price_bins=[round(p, 2) for p in bin_centers],
            volume_bins=[round(v, 2) for v in vol_distribution],
            summary=summary,
        )

    def _extract_arrays(self, candles: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Converts diverse candle structures into clean NumPy float64 arrays."""
        if hasattr(candles, "iloc"):
            # pandas DataFrame
            h = candles["high"].to_numpy(dtype=np.float64)
            l = candles["low"].to_numpy(dtype=np.float64)
            c = candles["close"].to_numpy(dtype=np.float64)
            v = candles["volume"].to_numpy(dtype=np.float64)
            return h, l, c, v

        if not candles:
            return np.array([]), np.array([]), np.array([]), np.array([])

        h_list, l_list, c_list, v_list = [], [], [], []
        for item in candles:
            if hasattr(item, "high"):
                # Bar object or dataclass
                h_list.append(float(item.high))
                l_list.append(float(item.low))
                c_list.append(float(item.close))
                v_list.append(float(item.volume))
            elif isinstance(item, dict):
                h_list.append(float(item.get("high", 0.0)))
                l_list.append(float(item.get("low", 0.0)))
                c_list.append(float(item.get("close", 0.0)))
                v_list.append(float(item.get("volume", 0.0)))
            elif isinstance(item, (list, tuple)) and len(item) >= 5:
                # Typical [time, open, high, low, close, volume]
                h_list.append(float(item[2]))
                l_list.append(float(item[3]))
                c_list.append(float(item[4]))
                v_list.append(float(item[5]))

        return (
            np.array(h_list, dtype=np.float64),
            np.array(l_list, dtype=np.float64),
            np.array(c_list, dtype=np.float64),
            np.array(v_list, dtype=np.float64),
        )
