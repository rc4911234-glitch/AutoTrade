"""Unit tests for production entrypoint and signal handling in main.py."""

import signal
import threading
from typing import Any
from unittest.mock import MagicMock, patch

from trad_auto.engine import TradingEngine
from trad_auto.main import main, parse_args, setup_signal_handlers


class TestMainEntrypoint:
    """Test suite for CLI argument parsing, signals, and main execution loop."""

    def test_parse_args_defaults(self) -> None:
        args = parse_args([])
        assert args.mode is None
        assert args.rehydrate is True
        assert args.daemon is False
        assert args.interactive is False
        assert args.health is False
        assert args.config is None

    def test_parse_args_custom_flags(self) -> None:
        args = parse_args(
            [
                "--mode",
                "PAPER",
                "--no-rehydrate",
                "--daemon",
                "--config",
                ".env.test",
            ]
        )
        assert args.mode == "PAPER"
        assert args.rehydrate is False
        assert args.daemon is True
        assert args.config == ".env.test"

    def test_main_health_flag(self) -> None:
        exit_code = main(["--health"])
        assert exit_code == 0

    def test_main_invalid_configuration_fails_closed(self) -> None:
        # LIVE mode without required confirmation phrase must exit with 1
        exit_code = main(["--mode", "LIVE"])
        assert exit_code == 1

    def test_setup_signal_handlers_triggers_engine_stop(self) -> None:
        mock_engine = MagicMock(spec=TradingEngine)
        shutdown_event = threading.Event()

        with patch("signal.signal") as mock_signal:
            setup_signal_handlers(mock_engine, shutdown_event)
            assert mock_signal.call_count >= 1

            # Extract handler passed to SIGINT and execute it
            handler = mock_signal.call_args_list[0][0][1]
            handler(signal.SIGINT, None)

            mock_engine.stop.assert_called_once()
            assert shutdown_event.is_set() is True

    def test_main_daemon_graceful_lifecycle(self) -> None:
        with patch("trad_auto.main.TradingEngine") as mock_engine_cls:
            mock_engine = MagicMock(spec=TradingEngine)
            mock_engine.get_health.return_value.status.value = "HEALTHY"
            mock_engine.webhook_server = None
            mock_engine_cls.return_value = mock_engine

            def fake_setup_signals(eng: Any, event: threading.Event) -> None:
                # Immediately set the event so daemon terminates cleanly after start
                event.set()

            with patch("trad_auto.main.setup_signal_handlers", side_effect=fake_setup_signals):
                exit_code = main(["--daemon", "--no-rehydrate"])
                assert exit_code == 0
                mock_engine.initialize.assert_called_once_with(rehydrate=False)
                mock_engine.start.assert_called_once()
                mock_engine.stop.assert_called()
