"""Production entrypoint and orchestration daemon for Trad-Auto."""

import argparse
import logging
import signal
import sys
import threading
from typing import Any

from config.settings import Settings, get_settings
from trad_auto.engine import TradingEngine
from trad_auto.health import ComponentStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("trad_auto.main")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Trad-Auto: Institutional-Grade Automated Quantitative Trading Engine"
    )
    parser.add_argument(
        "--mode",
        choices=["BACKTEST", "PAPER", "LIVE"],
        default=None,
        help="Operating execution mode (overrides environment settings)",
    )
    parser.add_argument(
        "--rehydrate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Restore session and portfolio state from SQLite on boot (default: True)",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        default=False,
        help="Run as non-interactive background daemon (default for Docker)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        default=False,
        help="Launch interactive terminal CLI REPL",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        default=False,
        help="Execute one-shot system health check and exit",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to custom environment file (.env)",
    )
    return parser.parse_args(args)


def setup_signal_handlers(
    engine: TradingEngine,
    shutdown_event: threading.Event,
) -> None:
    """Installs POSIX and Windows signal handlers for graceful shutdown."""

    def _handle_signal(signum: int, frame: Any) -> None:
        try:
            sig_name = signal.Signals(signum).name
        except Exception:
            sig_name = str(signum)

        logger.info("Received termination signal %s. Initiating graceful shutdown...", sig_name)
        engine.stop(reason=f"Signal {sig_name}")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)


def main(cli_args: list[str] | None = None) -> int:
    """Main application lifecycle runner."""
    args = parse_args(cli_args)

    # 1. One-shot Health Check mode
    if args.health:
        settings = get_settings()
        engine = TradingEngine(settings=settings)
        engine.initialize(rehydrate=False)
        report = engine.get_health()
        print(f"Health Status: {report.status.value} (is_healthy={report.is_healthy})")
        return 0 if report.is_healthy else 1

    # 2. Build configuration
    settings_kwargs: dict[str, Any] = {}
    if args.config:
        settings_kwargs["_env_file"] = args.config
    if args.mode:
        settings_kwargs["trading_mode"] = args.mode

    try:
        settings = Settings(**settings_kwargs) if settings_kwargs else get_settings()
    except Exception as exc:
        logger.fatal("Configuration validation failed: %s", exc)
        return 1

    logger.info("Starting Trad-Auto in %s mode...", settings.trading_mode)

    # 3. Instantiate Engine
    engine = TradingEngine(settings=settings)
    shutdown_event = threading.Event()
    setup_signal_handlers(engine, shutdown_event)

    try:
        # Initialize and Rehydrate
        engine.initialize(rehydrate=args.rehydrate)
        engine.start()

        # Check initial health
        health = engine.get_health()
        if health.status == ComponentStatus.UNHEALTHY:
            logger.error("Engine started with UNHEALTHY status. Review component vitals.")

        if getattr(engine, "webhook_server", None) is not None and settings.enable_web_dashboard:
            port = settings.webhook_port
            print(f"\n{'=' * 70}")
            print(f"  TRAD-AUTO LIVE DASHBOARD: http://localhost:{port}/dashboard")
            print(f"  Real-time Telemetry:      http://localhost:{port}/api/status")
            print(f"{'=' * 70}\n")

        # 4. Interactive REPL Mode vs Daemon Mode
        if args.interactive and not args.daemon:
            logger.info("Launching interactive CLI terminal...")
            engine.cli_adapter.run_loop()
        else:
            logger.info("Trad-Auto daemon running. Press Ctrl+C or send SIGTERM to stop.")
            while not shutdown_event.is_set():
                shutdown_event.wait(timeout=1.0)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught. Shutting down...")
    except Exception as exc:
        logger.fatal("Unhandled critical exception in TradingEngine: %s", exc, exc_info=True)
        engine.emergency_stop(reason=f"Unhandled crash: {exc}")
        return 1
    finally:
        engine.stop(reason="Main process termination")

    logger.info("Trad-Auto shutdown completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
