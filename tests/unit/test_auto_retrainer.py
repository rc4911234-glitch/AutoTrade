"""Unit tests for ModelAutoRetrainer background continual learning engine."""

from unittest.mock import MagicMock

from trad_auto.core.bus import EventBus
from trad_auto.core.events import QuantModelRetrainedEvent
from trad_auto.training.auto_retrainer import ModelAutoRetrainer
from trad_auto.training.trainer import BacktestPerformance, QuantModelTrainer, TrainingResult


def _make_mock_result(win_rate: float, profit_factor: float) -> TrainingResult:
    """Helper to build a deterministic TrainingResult."""
    perf = BacktestPerformance(
        total_trades=20,
        winning_trades=int(20 * (win_rate / 100.0)),
        losing_trades=20 - int(20 * (win_rate / 100.0)),
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        total_net_return_pct=1.5,
        max_drawdown_pct=0.4,
        sharpe_ratio=25.0,
    )
    return TrainingResult(
        model_path="data/models/btc_scalper_ml.joblib",
        metadata_path="data/models/btc_scalper_ml_metadata.json",
        feature_names=["f1", "f2"],
        sample_count=3000,
        decision_threshold=0.55,
        performance=perf,
    )


class TestModelAutoRetrainer:
    """Tests for ModelAutoRetrainer lifecycle, safety hurdles, and hot-swapping."""

    def test_auto_retrainer_init(self) -> None:
        trainer = MagicMock(spec=QuantModelTrainer)
        retrainer = ModelAutoRetrainer(
            trainer=trainer,
            interval_hours=12.0,
            candle_count=3000,
            min_win_rate_pct=55.0,
            min_profit_factor=1.30,
        )
        assert retrainer.interval_seconds == 12.0 * 3600.0
        assert retrainer.candle_count == 3000
        assert retrainer.min_win_rate_pct == 55.0
        assert retrainer.min_profit_factor == 1.30

    def test_auto_retrainer_hot_swaps_on_hurdle_pass(self) -> None:
        mock_trainer = MagicMock(spec=QuantModelTrainer)
        mock_trainer.train_pipeline.return_value = _make_mock_result(
            win_rate=65.0, profit_factor=2.10
        )
        fake_model = MagicMock()
        fake_meta = {"model_name": "btc_scalper_ml"}
        mock_trainer.load_model.return_value = (fake_model, fake_meta)

        mock_strategy = MagicMock()
        event_bus = EventBus()
        received_events: list[QuantModelRetrainedEvent] = []
        event_bus.subscribe(QuantModelRetrainedEvent, received_events.append)

        retrainer = ModelAutoRetrainer(
            trainer=mock_trainer,
            scalper_strategy=mock_strategy,
            event_bus=event_bus,
            min_win_rate_pct=50.0,
            min_profit_factor=1.20,
        )

        success, msg = retrainer.retrain_now()
        assert success is True
        assert "hot-swapped" in msg
        assert retrainer._consecutive_successes == 1
        assert retrainer._consecutive_failures == 0

        # Verify strategy hot-reloaded
        mock_strategy.hot_reload_model.assert_called_once_with(fake_model, fake_meta)

        # Verify event published
        assert len(received_events) == 1
        ev = received_events[0]
        assert ev.is_hot_swapped is True
        assert ev.win_rate_pct == 65.0
        assert ev.profit_factor == 2.10

    def test_auto_retrainer_preserves_on_hurdle_fail(self) -> None:
        mock_trainer = MagicMock(spec=QuantModelTrainer)
        # Underperforms hurdle (40% < 50%)
        mock_trainer.train_pipeline.return_value = _make_mock_result(
            win_rate=40.0, profit_factor=0.85
        )

        mock_strategy = MagicMock()
        event_bus = EventBus()
        received_events: list[QuantModelRetrainedEvent] = []
        event_bus.subscribe(QuantModelRetrainedEvent, received_events.append)

        retrainer = ModelAutoRetrainer(
            trainer=mock_trainer,
            scalper_strategy=mock_strategy,
            event_bus=event_bus,
            min_win_rate_pct=50.0,
            min_profit_factor=1.20,
        )

        success, msg = retrainer.retrain_now()
        assert success is False
        assert "underperformed" in msg
        assert retrainer._consecutive_failures == 1
        assert retrainer._consecutive_successes == 0

        # Verify strategy was NOT modified
        mock_strategy.hot_reload_model.assert_not_called()

        # Verify event published with is_hot_swapped=False
        assert len(received_events) == 1
        ev = received_events[0]
        assert ev.is_hot_swapped is False
        assert ev.win_rate_pct == 40.0

    def test_auto_retrainer_handles_exception(self) -> None:
        mock_trainer = MagicMock(spec=QuantModelTrainer)
        mock_trainer.train_pipeline.side_effect = RuntimeError("Binance API disconnected")

        retrainer = ModelAutoRetrainer(trainer=mock_trainer)
        success, msg = retrainer.retrain_now()

        assert success is False
        assert "Binance API disconnected" in msg
        assert retrainer._consecutive_failures == 1

    def test_auto_retrainer_lifecycle_start_stop(self) -> None:
        mock_trainer = MagicMock(spec=QuantModelTrainer)
        retrainer = ModelAutoRetrainer(
            trainer=mock_trainer,
            interval_hours=24.0,
        )

        assert retrainer._retrain_thread is None
        retrainer.start()
        assert retrainer._retrain_thread is not None
        assert retrainer._retrain_thread.is_alive()

        # Second start call does not spawn duplicate thread
        original_thread = retrainer._retrain_thread
        retrainer.start()
        assert retrainer._retrain_thread is original_thread

        retrainer.stop()
        assert retrainer._retrain_thread is None

    def test_auto_retrainer_telemetry(self) -> None:
        mock_trainer = MagicMock(spec=QuantModelTrainer)
        mock_trainer.train_pipeline.return_value = _make_mock_result(
            win_rate=60.0, profit_factor=1.75
        )
        retrainer = ModelAutoRetrainer(
            trainer=mock_trainer,
            interval_hours=24.0,
            candle_count=4000,
        )

        retrainer.retrain_now()
        telemetry = retrainer.get_telemetry()

        assert telemetry["interval_hours"] == 24.0
        assert telemetry["candle_count"] == 4000
        assert telemetry["consecutive_successes"] == 1
        assert telemetry["consecutive_failures"] == 0
        assert telemetry["last_performance"]["win_rate_pct"] == 60.0
        assert telemetry["last_performance"]["profit_factor"] == 1.75
