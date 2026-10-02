import warnings
import numpy as np
from typing import Any

# Suppress expected warnings from NaN slices during warmup period
warnings.filterwarnings("ignore", message=".*empty slice.*", category=RuntimeWarning)
warnings.filterwarnings("ignore", message=".*Degrees of freedom.*", category=RuntimeWarning)
warnings.filterwarnings("ignore", message=".*All-NaN slice.*", category=RuntimeWarning)

class Alpha158Engine:
    """Microsoft Qlib Alpha158 Factor Library - Institutional Grade.
    
    Computes 158 quantitative microstructure features from OHLCV bars.
    Each feature is documented, zero-future-leak, and vectorized.
    """
    
    FEATURE_NAMES: list[str] = []
    
    # Generate FEATURE_NAMES programmatically
    _KBAR = ['KMID', 'KLEN', 'KMID2', 'KUP', 'KUP2', 'KLOW', 'KLOW2', 'KSFT', 'KSFT2']
    _PRICE = ['OPEN0', 'HIGH0', 'LOW0', 'VWAP0']
    _ROLLING_BASES = [
        'ROC', 'MA', 'STD', 'BETA', 'RSQR', 'RESI', 'MAX', 'MIN', 'QTLU', 'QTLD',
        'RANK', 'RSV', 'IMAX', 'IMIN', 'IMXD', 'CORR', 'CORD', 'CNTP', 'CNTN',
        'CNTD', 'SUMP', 'SUMN', 'SUMD', 'VMA', 'VSTD', 'WVMA', 'VSUMP', 'VSUMN', 'VSUMD'
    ]
    _WINDOWS = [5, 10, 20, 30, 60]
    
    FEATURE_NAMES.extend(_KBAR)
    FEATURE_NAMES.extend(_PRICE)
    for w in _WINDOWS:
        for b in _ROLLING_BASES:
            FEATURE_NAMES.append(f"{b}_{w}")

    @staticmethod
    def _ref(a: np.ndarray, d: int) -> np.ndarray:
        out = np.full_like(a, np.nan)
        if d < len(a):
            out[d:] = a[:-d]
        return out
        
    @staticmethod
    def _rolling_mean(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.mean(wins, axis=1)
        return out

    @staticmethod
    def _rolling_std(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.std(wins, axis=1)
        return out

    @staticmethod
    def _rolling_sum(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.sum(wins, axis=1)
        return out

    @staticmethod
    def _rolling_max(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.max(wins, axis=1)
        return out

    @staticmethod
    def _rolling_min(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.min(wins, axis=1)
        return out

    @staticmethod
    def _rolling_argmax(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.argmax(wins, axis=1)
        return out

    @staticmethod
    def _rolling_argmin(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.argmin(wins, axis=1)
        return out

    @staticmethod
    def _rolling_quantile(a: np.ndarray, w: int, q: float) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            out[w-1:] = np.quantile(wins, q, axis=1)
        return out

    @staticmethod
    def _rolling_rank(a: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(a), np.nan)
        if len(a) >= w:
            wins = np.lib.stride_tricks.sliding_window_view(a, w)
            last_vals = wins[:, -1:]
            ranks = np.sum(wins <= last_vals, axis=1)
            out[w-1:] = ranks / w
        return out

    @staticmethod
    def _rolling_corr(x: np.ndarray, y: np.ndarray, w: int) -> np.ndarray:
        out = np.full(len(x), np.nan)
        if len(x) >= w:
            win_x = np.lib.stride_tricks.sliding_window_view(x, w)
            win_y = np.lib.stride_tricks.sliding_window_view(y, w)
            mean_x = np.mean(win_x, axis=1, keepdims=True)
            mean_y = np.mean(win_y, axis=1, keepdims=True)
            cov = np.mean((win_x - mean_x) * (win_y - mean_y), axis=1)
            std_x = np.std(win_x, axis=1)
            std_y = np.std(win_y, axis=1)
            denom = std_x * std_y
            denom = np.where(denom == 0, 1e-8, denom)
            out[w-1:] = cov / denom
        return out

    @staticmethod
    def _rolling_linear_regression(y: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        N = len(y)
        beta = np.full(N, np.nan)
        rsqr = np.full(N, np.nan)
        resi = np.full(N, np.nan)
        
        if N >= w:
            y_val = np.lib.stride_tricks.sliding_window_view(y, w)
            x = np.arange(w)
            mean_x = (w - 1) / 2
            var_x = (w**2 - 1) / 12
            mean_y = np.mean(y_val, axis=1, keepdims=True)
            
            cov_xy = np.mean((x - mean_x) * (y_val - mean_y), axis=1)
            b = cov_xy / var_x
            a = np.squeeze(mean_y) - b * mean_x
            
            pred = a + b * (w - 1)
            residual = y_val[:, -1] - pred
            
            ss_tot = np.sum((y_val - mean_y)**2, axis=1)
            preds = a[:, None] + b[:, None] * x[None, :]
            ss_res = np.sum((y_val - preds)**2, axis=1)
            
            ss_tot_safe = np.where(ss_tot == 0, 1e-8, ss_tot)
            r2 = 1 - (ss_res / ss_tot_safe)
            
            beta[w-1:] = b
            rsqr[w-1:] = r2
            resi[w-1:] = residual
            
        return beta, rsqr, resi

    def extract_features(
        self, candles: list[dict[str, Any]]
    ) -> tuple[np.ndarray, list[str], np.ndarray]:
        N = len(candles)
        warmup = max(self._WINDOWS)  # 60
        
        if N < warmup + 1:
            raise ValueError(f"Need at least {warmup + 1} candles for warmup, got {N}")
        
        features = np.full((N, 158), np.nan)
        open_p = np.array([float(c.get('open', 0)) for c in candles])
        high_p = np.array([float(c.get('high', 0)) for c in candles])
        low_p = np.array([float(c.get('low', 0)) for c in candles])
        close_p = np.array([float(c.get('close', 0)) for c in candles])
        vol_p = np.array([float(c.get('volume', 0)) for c in candles])
        
        eps = 1e-8
        
        # Category 1: KBar Features (9)
        f_idx = 0
        features[:, f_idx] = (close_p - open_p) / (close_p + eps); f_idx += 1  # KMID
        features[:, f_idx] = (high_p - low_p) / (close_p + eps); f_idx += 1  # KLEN
        features[:, f_idx] = (close_p - open_p) / (high_p - low_p + eps); f_idx += 1  # KMID2
        features[:, f_idx] = (high_p - np.maximum(open_p, close_p)) / (close_p + eps); f_idx += 1  # KUP
        features[:, f_idx] = (high_p - np.maximum(open_p, close_p)) / (high_p - low_p + eps); f_idx += 1  # KUP2
        features[:, f_idx] = (np.minimum(open_p, close_p) - low_p) / (close_p + eps); f_idx += 1  # KLOW
        features[:, f_idx] = (np.minimum(open_p, close_p) - low_p) / (high_p - low_p + eps); f_idx += 1  # KLOW2
        features[:, f_idx] = (2 * close_p - high_p - low_p) / (close_p + eps); f_idx += 1  # KSFT
        features[:, f_idx] = (2 * close_p - high_p - low_p) / (high_p - low_p + eps); f_idx += 1  # KSFT2
        
        # Category 2: Price Features (4)
        vwap_p = np.array([
            float(c.get('vwap', (c.get('high', 0) + c.get('low', 0) + c.get('close', 0)) / 3))
            for c in candles
        ])
        
        features[:, f_idx] = open_p / (close_p + eps); f_idx += 1
        features[:, f_idx] = high_p / (close_p + eps); f_idx += 1
        features[:, f_idx] = low_p / (close_p + eps); f_idx += 1
        features[:, f_idx] = vwap_p / (close_p + eps); f_idx += 1
        
        # Category 3: Rolling Statistics (29 * 5 = 145)
        for w in self._WINDOWS:
            # 1. ROC
            ref_close_w = self._ref(close_p, w)
            features[:, f_idx] = ref_close_w / (close_p + eps); f_idx += 1
            # 2. MA
            ma_close_w = self._rolling_mean(close_p, w)
            features[:, f_idx] = ma_close_w / (close_p + eps); f_idx += 1
            # 3. STD
            features[:, f_idx] = self._rolling_std(close_p, w) / (close_p + eps); f_idx += 1
            # 4, 5, 6. BETA, RSQR, RESI
            beta, rsqr, resi = self._rolling_linear_regression(close_p, w)
            features[:, f_idx] = beta; f_idx += 1
            features[:, f_idx] = rsqr; f_idx += 1
            features[:, f_idx] = resi / (close_p + eps); f_idx += 1
            # 7. MAX
            max_high_w = self._rolling_max(high_p, w)
            features[:, f_idx] = max_high_w / (close_p + eps); f_idx += 1
            # 8. MIN
            min_low_w = self._rolling_min(low_p, w)
            features[:, f_idx] = min_low_w / (close_p + eps); f_idx += 1
            # 9. QTLU
            features[:, f_idx] = self._rolling_quantile(close_p, w, 0.8) / (close_p + eps); f_idx += 1
            # 10. QTLD
            features[:, f_idx] = self._rolling_quantile(close_p, w, 0.2) / (close_p + eps); f_idx += 1
            # 11. RANK
            features[:, f_idx] = self._rolling_rank(close_p, w); f_idx += 1
            # 12. RSV
            features[:, f_idx] = (close_p - min_low_w) / (max_high_w - min_low_w + eps); f_idx += 1
            # 13. IMAX
            imax_w = self._rolling_argmax(high_p, w)
            features[:, f_idx] = imax_w / w; f_idx += 1
            # 14. IMIN
            imin_w = self._rolling_argmin(low_p, w)
            features[:, f_idx] = imin_w / w; f_idx += 1
            # 15. IMXD
            features[:, f_idx] = (imax_w - imin_w) / w; f_idx += 1
            # 16. CORR
            features[:, f_idx] = self._rolling_corr(close_p, np.log1p(vol_p), w); f_idx += 1
            # 17. CORD
            ref_close_1 = self._ref(close_p, 1)
            ref_vol_1 = self._ref(vol_p, 1)
            cord_x = close_p / (ref_close_1 + eps)
            cord_y = np.log1p(vol_p / (ref_vol_1 + eps))
            features[:, f_idx] = self._rolling_corr(cord_x, cord_y, w); f_idx += 1
            # 18. CNTP
            cnt_p_bool = (close_p > ref_close_1).astype(float)
            cntp_w = self._rolling_mean(cnt_p_bool, w)
            features[:, f_idx] = cntp_w; f_idx += 1
            # 19. CNTN
            cnt_n_bool = (close_p < ref_close_1).astype(float)
            cntn_w = self._rolling_mean(cnt_n_bool, w)
            features[:, f_idx] = cntn_w; f_idx += 1
            # 20. CNTD
            features[:, f_idx] = cntp_w - cntn_w; f_idx += 1
            # 21. SUMP
            diff_c = close_p - ref_close_1
            sump_num = self._rolling_sum(np.maximum(diff_c, 0), w)
            sum_abs_diff_c = self._rolling_sum(np.abs(diff_c), w)
            sump_w = sump_num / (sum_abs_diff_c + eps)
            features[:, f_idx] = sump_w; f_idx += 1
            # 22. SUMN
            sumn_num = self._rolling_sum(np.maximum(-diff_c, 0), w)
            sumn_w = sumn_num / (sum_abs_diff_c + eps)
            features[:, f_idx] = sumn_w; f_idx += 1
            # 23. SUMD
            features[:, f_idx] = sump_w - sumn_w; f_idx += 1
            # 24. VMA
            vma_w = self._rolling_mean(vol_p, w)
            features[:, f_idx] = vma_w / (vol_p + eps); f_idx += 1
            # 25. VSTD
            features[:, f_idx] = self._rolling_std(vol_p, w) / (vol_p + eps); f_idx += 1
            # 26. WVMA
            features[:, f_idx] = self._rolling_std(close_p / (ma_close_w + eps), w); f_idx += 1
            # 27. VSUMP
            diff_v = vol_p - ref_vol_1
            vsump_num = self._rolling_sum(np.maximum(diff_v, 0), w)
            sum_abs_diff_v = self._rolling_sum(np.abs(diff_v), w)
            vsump_w = vsump_num / (sum_abs_diff_v + eps)
            features[:, f_idx] = vsump_w; f_idx += 1
            # 28. VSUMN
            vsumn_num = self._rolling_sum(np.maximum(-diff_v, 0), w)
            vsumn_w = vsumn_num / (sum_abs_diff_v + eps)
            features[:, f_idx] = vsumn_w; f_idx += 1
            # 29. VSUMD
            features[:, f_idx] = vsump_w - vsumn_w; f_idx += 1

        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
        
        valid_indices = np.arange(warmup, N) if N > warmup else np.array([], dtype=int)
        X = features[valid_indices]
        
        return X, self.FEATURE_NAMES, valid_indices

    def extract_features_multi_asset(
        self, 
        asset_candles: dict[str, list[dict]]
    ) -> dict[str, tuple[np.ndarray, list[str], np.ndarray]]:
        result = {}
        for symbol, candles in asset_candles.items():
            result[symbol] = self.extract_features(candles)
        return result
