"""Autonomous background continuous machine learning model retrainer for Trad-Auto."""

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from trad_auto.core.bus import EventBus
from trad_auto.core.events import QuantModelRetrainedEvent
from trad_auto.training.trainer import QuantModelTrainer, TrainingResult

logger = logging.getLogger(__name__)


class ModelAutoRetrainer:
    """Continuously evaluates market data and retrains the Quant ML Model.

    Features:
    - Non-blocking daemon background worker thread.
    - Safety hurdle check: Only hot-swaps candidate model if WinRate >= 50% & ProfitFactor >= 1.20.
    - Preserves existing proven model if candidate fails validation.
    - Publishes QuantModelRetrainedEvent across EventBus.
    - Thread-safe on-demand manual trigger via retrain_now().
    """

    def __init__(
        self,
        trainer: QuantModelTrainer | None = None,
        scalper_strategy: Any = None,
        event_bus: EventBus | None = None,
        interval_hours: float = 24.0,
        candle_count: int = 5000,
        min_win_rate_pct: float = 50.0,
        min_profit_factor: float = 1.20,
    ) -> None:
        self.trainer = trainer or QuantModelTrainer()
        self.scalper_strategy = scalper_strategy
        self.event_bus = event_bus
        self.interval_seconds = max(60.0, interval_hours * 3600.0)
        self.candle_count = candle_count
        self.min_win_rate_pct = min_win_rate_pct
        self.min_profit_factor = min_profit_factor

        self._stop_event = threading.Event()
        self._retrain_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._last_retrain_time: datetime | None = None
        self._last_result: TrainingResult | None = None
        self._consecutive_successes: int = 0
        self._consecutive_failures: int = 0

    def start(self) -> None:
        """Starts the autonomous background retraining loop."""
        if self._retrain_thread is not None and self._retrain_thread.is_alive():
            logger.warning("ModelAutoRetrainer is already running.")
            return

        self._stop_event.clear()
        self._retrain_thread = threading.Thread(
            target=self._retrain_loop,
            name="QuantModelAutoRetrainerThread",
            daemon=True,
        )
        self._retrain_thread.start()
        logger.info(
            "ModelAutoRetrainer daemon thread started (interval=%.1fh, target=%d candles).",
            self.interval_seconds / 3600.0,
            self.candle_count,
        )

    def stop(self) -> None:
        """Stops the autonomous retraining thread gracefully."""
        self._stop_event.set()
        if self._retrain_thread is not None and self._retrain_thread.is_alive():
            self._retrain_thread.join(timeout=5.0)
            self._retrain_thread = None
        logger.info("ModelAutoRetrainer stopped.")

    def retrain_now(self) -> tuple[bool, str]:
        """Synchronously triggers an on-demand retraining cycle with safety gate."""
        with self._lock:
            logger.info(
                "Executing model retraining cycle (Asset: BTCUSDT, Samples: %d)...",
                self.candle_count,
            )
            try:
                result = self.trainer.train_pipeline(
                    candle_count=self.candle_count,
                    model_name="btc_scalper_ml",
                )
                self._last_retrain_time = datetime.now(UTC)
                self._last_result = result
                perf = result.performance

                # Safety Hurdle Check
                meets_hurdle = (
                    perf.win_rate_pct >= self.min_win_rate_pct
                    and perf.profit_factor >= self.min_profit_factor
                )

                if meets_hurdle:
                    self._consecutive_successes += 1
                    self._consecutive_failures = 0
                    logger.info(
                        "Candidate model passed safety hurdle (WinRate=%.1f%%, PF=%.2f). "
                        "Hot-swapping live strategy...",
                        perf.win_rate_pct,
                        perf.profit_factor,
                    )

                    # Hot-swap live strategy model
                    if (
                        self.scalper_strategy is not None
                        and hasattr(self.scalper_strategy, "hot_reload_model")
                    ):
                        loaded = self.trainer.load_model("btc_scalper_ml")
                        if loaded is not None:
                            mod, meta = loaded
                            self.scalper_strategy.hot_reload_model(mod, meta)

                    if self.event_bus is not None:
                        self.event_bus.publish(
                            QuantModelRetrainedEvent(
                                model_name="btc_scalper_ml",
                                sample_count=result.sample_count,
                                win_rate_pct=perf.win_rate_pct,
                                profit_factor=perf.profit_factor,
                                sharpe_ratio=perf.sharpe_ratio,
                                net_return_pct=perf.total_net_return_pct,
                                is_hot_swapped=True,
                            )
                        )

                    return (
                        True,
                        f"Model retrained & hot-swapped: WinRate={perf.win_rate_pct:.1f}%, "
                        f"PF={perf.profit_factor:.2f}, "
                        f"NetReturn={perf.total_net_return_pct:+.2f}%",
                    )
                else:
                    self._consecutive_failures += 1
                    logger.warning(
                        "Candidate model rejected: WinRate=%.1f%% (min=%.1f%%) or "
                        "PF=%.2f (min=%.2f). Existing proven model retained.",
                        perf.win_rate_pct,
                        self.min_win_rate_pct,
                        perf.profit_factor,
                        self.min_profit_factor,
                    )
                    if self.event_bus is not None:
                        self.event_bus.publish(
                            QuantModelRetrainedEvent(
                                model_name="btc_scalper_ml",
                                sample_count=result.sample_count,
                                win_rate_pct=perf.win_rate_pct,
                                profit_factor=perf.profit_factor,
                                sharpe_ratio=perf.sharpe_ratio,
                                net_return_pct=perf.total_net_return_pct,
                                is_hot_swapped=False,
                            )
                        )
                    return (
                        False,
                        f"Candidate model underperformed hurdle (WinRate={perf.win_rate_pct:.1f}%, "
                        f"PF={perf.profit_factor:.2f}). Proven model retained.",
                    )
            except Exception as exc:
                self._consecutive_failures += 1
                logger.error("Auto-retraining cycle failed with exception: %s", exc)
                return False, f"Retraining failed: {exc}"

    def _retrain_loop(self) -> None:
        """Internal daemon loop sleeping for interval and triggering retrain."""
        while not self._stop_event.is_set():
            # Wait for next interval or stop signal
            if self._stop_event.wait(timeout=self.interval_seconds):
                break
            try:
                self.retrain_now()
            except Exception as exc:
                logger.error("Error in background auto-retrain worker: %s", exc)

    def get_telemetry(self) -> dict[str, Any]:
        """Provides status dictionary for dashboard telemetry and health monitor."""
        return {
            "is_running": (
                self._retrain_thread is not None and self._retrain_thread.is_alive()
            ),
            "interval_hours": self.interval_seconds / 3600.0,
            "candle_count": self.candle_count,
            "last_retrain_time": (
                self._last_retrain_time.isoformat() if self._last_retrain_time else None
            ),
            "consecutive_successes": self._consecutive_successes,
            "consecutive_failures": self._consecutive_failures,
            "last_performance": (
                {
                    "win_rate_pct": self._last_result.performance.win_rate_pct,
                    "profit_factor": self._last_result.performance.profit_factor,
                    "net_return_pct": self._last_result.performance.total_net_return_pct,
                    "sharpe_ratio": self._last_result.performance.sharpe_ratio,
                }
                if self._last_result is not None
                else None
            ),
        }
