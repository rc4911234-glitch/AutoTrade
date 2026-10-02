"""Marcos López de Prado's Fractional Differentiation Engine for Financial Time Series.

Reference:
- Marcos López de Prado, "Advances in Financial Machine Learning", Chapter 5:
  "Fractionally Differentiated Features" (Wiley, 2018).

Core Concept:
Integer differencing (d=1, returns) achieves stationarity at the cost of erasing 100%
of historical price memory.
Fractional differencing (0 < d < 1) achieves mathematical stationarity (passes ADF test)
while preserving the maximum degree of multi-period price memory and trend support.
"""

from typing import Sequence
import numpy as np


class FractionalDifferentiator:
    """Computes Fractionally Differentiated price series using Fixed-Width Window (FFD)."""

    def __init__(self, d: float = 0.40, threshold: float = 1e-4) -> None:
        """Initializes fractional differentiator.

        Args:
            d: Fractional differencing degree (typically 0.35 to 0.45 for crypto).
            threshold: Minimum weight threshold tau for window truncation.
        """
        self.d = d
        self.threshold = threshold
        self.weights = self._compute_weights(d, threshold)

    @staticmethod
    def _compute_weights(d: float, threshold: float) -> np.ndarray:
        """Computes iterative binomial expansion weights for (1 - B)^d.

        w_0 = 1
        w_k = -w_{k-1} * (d - k + 1) / k
        """
        w = [1.0]
        k = 1
        while True:
            w_k = -w[-1] * (d - k + 1.0) / k
            if abs(w_k) < threshold:
                break
            w.append(w_k)
            k += 1
            if k > 500:  # Safety ceiling
                break
        return np.array(w[::-1], dtype=np.float64)  # Reverse for convolution

    def transform(self, series: Sequence[float] | np.ndarray) -> np.ndarray:
        """Applies fractional differentiation to a 1D price time series with zero look-ahead.

        Args:
            series: 1D array or sequence of historical prices.

        Returns:
            np.ndarray: Fractionally differentiated series (same length as input,
                        initial warmup points set to 0.0 or nan).
        """
        arr = np.asarray(series, dtype=np.float64)
        n = len(arr)
        window = len(self.weights)
        output = np.zeros(n, dtype=np.float64)

        if n < window:
            # Fallback simple difference if series shorter than weight window
            output[1:] = np.diff(arr)
            return output

        # Vectorized dot product sliding window
        for i in range(window - 1, n):
            window_slice = arr[i - window + 1 : i + 1]
            output[i] = np.dot(self.weights, window_slice)

        return output

    def get_memory_weight_ratio(self) -> float:
        """Returns the ratio of memory preserved relative to raw price series."""
        # Sum of absolute weights after lag 1 indicates preserved memory depth
        if len(self.weights) <= 1:
            return 0.0
        return float(np.sum(np.abs(self.weights[:-1])) / np.sum(np.abs(self.weights)))
