"""Autonomous 24/7 Paper Trading Soak Test Daemon for Trad-Auto.

Runs uninterrupted in the background:
- Scans BTC, ETH, SOL, BNB with Microsoft Qlib Alpha158 + Cross-Sectional Multi-Asset Ranker.
- Executes SmartMoneyScalper trades with the 158-feature Stacking Ensemble ML model.
- Evaluates Statistical Arbitrage (BTC/ETH Cointegration Pairs) for market-neutral mean-reversion.
- Enforces 1:2 R:R bracket geometry and BlackRock Aladdin tail risk guards.
- Writes all trade post-mortems, reflections, and lessons directly to Supabase PostgreSQL.
"""

from decimal import Decimal
import logging
import os
import signal
import sys
import time
from typing import Any
from uuid import uuid4

from config.settings import get_settings
from trad_auto.core.enums import SessionState, TradingMode
from trad_auto.core.models.session import FinancialLimits
from trad_auto.engine import TradingEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("trad_auto.soak_test")


def main() -> int:
    settings = get_settings()
    settings.trading_mode = "PAPER"
    settings.enable_web_dashboard = False

    # Ensure Supabase PostgreSQL persistence
    supabase_url = (
        "postgresql://postgres.zewxjwmqowbpdppnixjc:Tradeauto%405755"
        "@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    if not settings.database_url or "sqlite" in settings.database_url.lower():
        settings.database_url = supabase_url

    print("=" * 70)
    print("  TRAD-AUTO: 24/7 AUTONOMOUS PAPER TRADING SOAK TEST")
    print(f"  Mode:             {settings.trading_mode}")
    print(f"  Database:         Supabase PostgreSQL (Cloud Synced)")
    print(f"  Quant Features:   Microsoft Qlib Alpha158 (158 Microstructure Factors)")
    print(f"  Alpha Selection:  Cross-Sectional Multi-Asset Ranker (BTC, ETH, SOL, BNB)")
    print(f"  Pairs Trading:    Statistical Arbitrage (BTC/ETH Cointegration Z-Score)")
    print("=" * 70)

    engine = TradingEngine(settings=settings)
    engine.initialize(rehydrate=True)
    engine.start()

    # Graceful shutdown handler
    running = True

    def _sig_handler(sig: int, frame: Any) -> None:
        nonlocal running
        logger.info("Termination signal received. Shutting down soak test...")
        running = False

    signal.signal(signal.SIGINT, _sig_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sig_handler)

    # Activate Paper Session with 10,000 USDT authorized capital
    time.sleep(2)
    if engine.session_manager.state != SessionState.TRADING:
        logger.info("Activating Paper Trading session with $10,000 USDT capital limit...")
        engine.session_manager.activate_session(
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("10000.00")),
            owner_command_id=uuid4(),
            current_time=engine.clock.now(),
        )

    logger.info("Paper session is LIVE and scanning markets 24/7.")
    last_heartbeat = 0.0
    heartbeat_interval = 60.0  # Log summary every 60 seconds

    try:
        while running:
            now_ts = time.time()
            if now_ts - last_heartbeat >= heartbeat_interval:
                last_heartbeat = now_ts
                snap = engine.get_dashboard_snapshot()
                bal = float(snap.get("usdt_balance") or 10000.00)
                rpnl = float(snap.get("today_realized_pnl") or 0.00)
                upnl = float(snap.get("current_unrealized_pnl") or 0.00)
                open_pos = len(snap.get("open_positions", []))

                # Extract Strategy info
                s_info = snap.get("strategy_info", {})
                s_scalper = s_info.get("smart_money_scalper", {})
                top_asset = s_scalper.get("cs_top_asset", "BTCUSDT")
                regime = s_scalper.get("cs_regime", "normal")

                # Stat-Arb telemetry
                stat_arb = engine.strategy_manager.get_strategy("stat_arb_btc_eth")
                sa_telemetry = stat_arb.get_telemetry() if stat_arb and hasattr(stat_arb, "get_telemetry") else {}
                sa_z = sa_telemetry.get("z_score", 0.0)
                sa_sig = sa_telemetry.get("signal", "NEUTRAL")

                # Brain journal
                brain = snap.get("brain", {})
                total_trades = brain.get("total_today_trades", 0)

                logger.info(
                    "[SOAK HEARTBEAT] Equity: $%.2f | Realized PnL: %+.2f | Open Positions: %d | "
                    "CS #1 King: %s (Regime: %s) | Stat-Arb Z: %+.2f (%s) | Total Trades: %d",
                    bal,
                    rpnl,
                    open_pos,
                    top_asset,
                    regime,
                    sa_z,
                    sa_sig,
                    total_trades,
                )

            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        logger.info("Stopping Trading Engine...")
        engine.stop(reason="Soak test stopped by user")
        logger.info("Engine stopped. All state safely flushed to database.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
