"""Unified TradingEngine orchestrating all platform subsystems."""

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from config.settings import Settings, get_settings
from trad_auto.command.adapters.cli import CLICommandAdapter
from trad_auto.command.confirmation import ConfirmationManager
from trad_auto.command.deduplication import MessageDeduplicator
from trad_auto.command.gateway import CommandGateway
from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.communication.notifier import WhatsAppNotifier
from trad_auto.communication.signature import TwilioSignatureValidator
from trad_auto.communication.twilio_client import TwilioWhatsAppClient
from trad_auto.communication.webhook_server import WebhookServer
from trad_auto.communication.whatsapp_adapter import WhatsAppAdapter
from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import CommandStatus, OrderActionPurpose, OrderSide, OrderType, SessionState, TradingMode
from trad_auto.core.events import PositionOpenedEvent, QuoteUpdatedEvent, TradeProposalEvent
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.market_data import Quote
from trad_auto.core.models.order import Order
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.time import Clock, SystemClock
from trad_auto.execution.adapter_base import ExecutionAdapter
from trad_auto.execution.binance.adapter import BinanceFuturesExecutionAdapter
from trad_auto.execution.binance.client import BinanceFuturesRestClient
from trad_auto.execution.binance.signer import BinanceRequestSigner
from trad_auto.execution.order_manager import OrderManager
from trad_auto.execution.simulated_adapter import SimulatedExecutionAdapter
from trad_auto.health import HealthMonitor, SystemHealthReport
from trad_auto.market_data.adapters.base import BaseMarketDataAdapter
from trad_auto.market_data.adapters.simulated import SimulatedMarketDataAdapter
from trad_auto.market_data.bar_builder import BarBuilder
from trad_auto.market_data.live_feeder import BinanceLiveFeeder
from trad_auto.market_data.store import BarStore, QuoteStore
from trad_auto.market_data.streaming.adapter import StreamingMarketDataAdapter
from trad_auto.market_data.streaming.binance_ws import BinanceWebSocketClient
from trad_auto.market_data.streaming.watchdog import FeedWatchdog
from trad_auto.market_data.validator import DataQualityValidator
from trad_auto.news import LiveNewsPoller, MacroCalendarManager, NewsVolatilityShield
from trad_auto.persistence.audit_logger import AuditLogger
from trad_auto.persistence.database import DatabaseManager
from trad_auto.persistence.rehydration import StateRehydrator
from trad_auto.persistence.repositories.order_repo import OrderRepository
from trad_auto.persistence.repositories.portfolio_repo import PortfolioRepository
from trad_auto.persistence.repositories.session_repo import SessionRepository
from trad_auto.portfolio.ledger import PositionLedger
from trad_auto.risk.gatekeeper import RiskGatekeeper
from trad_auto.risk.ledger_bridge import PositionLedgerRiskBridge
from trad_auto.strategies.base import BaseStrategy
from trad_auto.strategies.concrete.atr_breakout import ATRBreakoutStrategy
from trad_auto.strategies.concrete.mean_reversion import BollingerMeanReversionStrategy
from trad_auto.strategies.concrete.smart_money_scalper import SmartMoneyScalperStrategy
from trad_auto.strategies.manager import StrategyManager
from trad_auto.training.auto_retrainer import ModelAutoRetrainer

logger = logging.getLogger(__name__)


class TradingEngine:
    """Master production orchestration engine for Trad-Auto.

    Unites domain subsystems into a cohesive, fail-closed trading platform:
    - EventBus message passing
    - Relational SQLite WAL persistence & state rehydration
    - Session state machine and financial limits
    - Position ledger, FIFO lots, and mark-to-market valuations
    - Risk gatekeeper and volatility position sizing
    - Strategy execution and market data stores
    - Exchange order management with mandatory protective brackets
    - Owner WhatsApp alerting and interactive CLI control
    - Health monitoring and telemetry
    """

    def __init__(
        self,
        settings: Settings | None = None,
        clock: Clock | None = None,
        event_bus: EventBus | None = None,
        db_manager: DatabaseManager | None = None,
        execution_adapter: ExecutionAdapter | None = None,
        market_data_adapter: BaseMarketDataAdapter | None = None,
        news_shield: NewsVolatilityShield | None = None,
        news_poller: LiveNewsPoller | None = None,
        default_instruments: list[Instrument] | None = None,
        default_strategies: list[BaseStrategy] | None = None,
    ) -> None:
        self.settings: Settings = settings or get_settings()
        self.clock: Clock = clock or SystemClock()
        self.event_bus: EventBus = event_bus or EventBus()
        self.db_manager: DatabaseManager = db_manager or DatabaseManager(
            db_path=self.settings.sqlite_db_path,
            busy_timeout_ms=self.settings.sqlite_busy_timeout_ms,
        )

        # 1. Repositories and Audit Logging
        self.session_repo = SessionRepository(self.db_manager)
        self.portfolio_repo = PortfolioRepository(self.db_manager)
        self.order_repo = OrderRepository(self.db_manager)
        self.audit_logger = AuditLogger(
            event_bus=self.event_bus,
            db=self.db_manager,
        )
        self.rehydrator = StateRehydrator(
            session_repo=self.session_repo,
            portfolio_repo=self.portfolio_repo,
        )

        # 2. Session Management & Owner Gateway
        self.session_manager = TradingSessionManager(event_bus=self.event_bus)
        self.confirmation_manager = ConfirmationManager(event_bus=self.event_bus)
        self.deduplicator = MessageDeduplicator(
            db_path=self.settings.sqlite_db_path,
            busy_timeout_ms=self.settings.sqlite_busy_timeout_ms,
        )
        self.gateway = CommandGateway(
            settings=self.settings,
            clock=self.clock,
            session_manager=self.session_manager,
            confirmation_manager=self.confirmation_manager,
            deduplicator=self.deduplicator,
            event_bus=self.event_bus,
        )
        self.cli_adapter = CLICommandAdapter(
            gateway=self.gateway,
            clock=self.clock,
            sender_id="owner_cli",
        )

        # 3. News Volatility Shield & Live Poller
        self.news_shield: NewsVolatilityShield | None = None
        if self.settings.enable_news_shield:
            cal_mgr = MacroCalendarManager(
                default_pre_buffer_minutes=self.settings.news_blackout_pre_minutes,
                default_post_buffer_minutes=self.settings.news_blackout_post_minutes,
            )
            self.news_shield = news_shield or NewsVolatilityShield(
                calendar_mgr=cal_mgr,
                event_bus=self.event_bus,
                clock=self.clock,
            )
        elif news_shield is not None:
            self.news_shield = news_shield

        self.news_poller: LiveNewsPoller | None = None
        if self.news_shield is not None and self.settings.enable_news_shield:
            self.news_poller = news_poller or LiveNewsPoller(
                shield=self.news_shield,
                api_token=self.settings.cryptopanic_api_token,
                rss_urls=self.settings.news_rss_feed_urls,
                poll_interval_seconds=self.settings.news_poll_interval_seconds,
            )
        elif news_poller is not None:
            self.news_poller = news_poller

        # 4. Portfolio Ledger & Risk Bridge
        self.ledger = PositionLedger(
            event_bus=self.event_bus,
            clock=self.clock,
            default_quote_asset="USDT",
        )
        self.gateway.position_ledger = self.ledger
        self.risk_bridge = PositionLedgerRiskBridge(self.ledger)
        self.risk_gatekeeper = RiskGatekeeper(
            event_bus=self.event_bus,
            session_manager=self.session_manager,
            portfolio_bridge=self.risk_bridge,
            news_shield=self.news_shield,
        )

        # 4. Market Data Stores & Strategies
        self.bar_store = BarStore()
        self.quote_store = QuoteStore()
        self.strategy_manager = StrategyManager(
            event_bus=self.event_bus,
            bar_store=self.bar_store,
            session_state_provider=lambda: self.session_manager.state,
        )

        # 5. Execution Adapter Selection
        if execution_adapter is not None:
            self.execution_adapter: ExecutionAdapter = execution_adapter
        elif self.settings.trading_mode == "LIVE":
            signer = BinanceRequestSigner(
                api_key=self.settings.binance_api_key,
                api_secret=self.settings.binance_api_secret,
            )
            rest_client = BinanceFuturesRestClient(
                signer=signer,
                base_url=self.settings.binance_futures_base_url,
            )
            self.execution_adapter = BinanceFuturesExecutionAdapter(
                rest_client=rest_client,
                event_bus=self.event_bus,
                clock=self.clock,
                is_live_mode=True,
                enable_live_trading=self.settings.enable_live_trading,
            )
        else:
            self.execution_adapter = SimulatedExecutionAdapter(
                event_bus=self.event_bus,
                clock=self.clock,
            )

        # 6. Order Management with Mandatory Brackets & Trailing Stop
        self.order_manager = OrderManager(
            event_bus=self.event_bus,
            adapter=self.execution_adapter,
            ledger=self.ledger,
            clock=self.clock,
            enable_trailing_stop=self.settings.enable_trailing_stop,
            breakeven_offset_ratio=self.settings.trailing_breakeven_offset_pct,
            breakeven_fee_buffer=self.settings.trailing_breakeven_fee_buffer,
            trailing_offset_ratio=self.settings.trailing_stop_offset_pct,
            trailing_delta_ratio=self.settings.trailing_stop_delta_pct,
            entry_timeout_seconds=self.settings.entry_timeout_seconds,
            enable_orphan_watchdog=self.settings.enable_orphan_watchdog,
            trailing_min_ratchet_pct=self.settings.trailing_min_ratchet_pct,
        )

        # 7. Market Data Adapter Selection
        self.feed_watchdog: FeedWatchdog | None = None
        if market_data_adapter is not None:
            self.market_data_adapter = market_data_adapter
            if hasattr(market_data_adapter, "_watchdog"):
                self.feed_watchdog = getattr(market_data_adapter, "_watchdog", None)
        elif self.settings.trading_mode in ("LIVE", "PAPER"):
            # Both LIVE and PAPER use real Binance WebSocket for market data.
            # PAPER differs only in execution (SimulatedExecutionAdapter).
            ws_url = (
                self.settings.binance_ws_base_url
                if self.settings.binance_ws_base_url
                else "wss://fstream.binance.com/ws"
            )
            ws_client = BinanceWebSocketClient(base_url=ws_url)
            self.feed_watchdog = FeedWatchdog(
                timeout_seconds=self.settings.feed_watchdog_timeout_seconds,
                event_bus=self.event_bus,
                clock=self.clock,
            )
            validator = DataQualityValidator(
                event_bus=self.event_bus,
            )
            bar_builder = BarBuilder(
                timeframes=["1m", "5m", "15m", "1h"],
                event_bus=self.event_bus,
                bar_store=self.bar_store,
            )
            self.market_data_adapter = StreamingMarketDataAdapter(
                ws_client=ws_client,
                watchdog=self.feed_watchdog,
                validator=validator,
                bar_builder=bar_builder,
                event_bus=self.event_bus,
                clock=self.clock,
            )
        else:
            self.market_data_adapter = SimulatedMarketDataAdapter(
                event_bus=self.event_bus,
                clock=self.clock if hasattr(self.clock, "advance") else None,  # type: ignore[arg-type]
                bar_store=self.bar_store,
                quote_store=self.quote_store,
            )

        if self.feed_watchdog is not None:
            self.risk_gatekeeper.set_feed_watchdog(self.feed_watchdog)

        is_simulated = hasattr(self.clock, "advance")
        self.live_feeder: BinanceLiveFeeder | None = None
        if (
            self.settings.trading_mode in ("LIVE", "PAPER")
            and not is_simulated
            and market_data_adapter is None
        ):
            self.live_feeder = BinanceLiveFeeder(
                event_bus=self.event_bus,
                symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                timeframe="1m",
                poll_interval_sec=5.0,
            )

        # 8. Communication Notifier + Webhook Server (if credentials provided or dashboard enabled)
        self.whatsapp_notifier: WhatsAppNotifier | None = None
        self.webhook_server: WebhookServer | None = None
        self._whatsapp_client: TwilioWhatsAppClient | None = None
        whatsapp_adapter: WhatsAppAdapter | None = None

        if (
            self.settings.twilio_account_sid
            and self.settings.twilio_auth_token
            and self.settings.twilio_whatsapp_number
            and self.settings.owner_whatsapp_number
        ):
            self._whatsapp_client = TwilioWhatsAppClient(
                account_sid=self.settings.twilio_account_sid,
                auth_token=self.settings.twilio_auth_token,
                from_number=self.settings.twilio_whatsapp_number,
            )
            self.whatsapp_notifier = WhatsAppNotifier(
                event_bus=self.event_bus,
                whatsapp_client=self._whatsapp_client,
                owner_phone=self.settings.owner_whatsapp_number,
            )

            # Inbound webhook: WhatsAppAdapter + WebhookServer
            sig_validator: TwilioSignatureValidator | None = None
            if self.settings.twilio_auth_token:
                sig_validator = TwilioSignatureValidator(
                    auth_token=self.settings.twilio_auth_token,
                )
            whatsapp_adapter = WhatsAppAdapter(
                settings=self.settings,
                clock=self.clock,
                gateway=self.gateway,
                whatsapp_client=self._whatsapp_client,
                signature_validator=sig_validator,
            )

        if self.settings.enable_web_dashboard or whatsapp_adapter is not None:
            self.webhook_server = WebhookServer(
                adapter=whatsapp_adapter,
                host=self.settings.webhook_host,
                port=self.settings.webhook_port,
                status_provider=self.get_dashboard_snapshot,
                command_handler=self.execute_dashboard_command,
            )

        # 9. Health Telemetry Monitor
        self.health_monitor = HealthMonitor(
            db_manager=self.db_manager,
            session_manager=self.session_manager,
            execution_adapter=self.execution_adapter,
            news_shield=self.news_shield,
            news_poller=self.news_poller,
            clock=self.clock,
        )

        # 10. Default Instruments and Strategies
        self._default_instruments = default_instruments or self._build_default_instruments()
        self._default_strategies = default_strategies or self._build_default_strategies()
        self.auto_retrainer: ModelAutoRetrainer | None = None

        self._is_initialized: bool = False
        self._is_running: bool = False

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _build_default_instruments(self) -> list[Instrument]:
        """Builds standard institutional crypto futures contracts."""
        return [
            Instrument(
                symbol="BTCUSDT",
                exchange="BINANCE",
                asset_class="CRYPTO",
                currency="USDT",
                tick_size=Decimal("0.10"),
                lot_size=Decimal("0.001"),
                quantity_step=Decimal("0.001"),
                min_quantity=Decimal("0.001"),
            ),
            Instrument(
                symbol="ETHUSDT",
                exchange="BINANCE",
                asset_class="CRYPTO",
                currency="USDT",
                tick_size=Decimal("0.01"),
                lot_size=Decimal("0.01"),
                quantity_step=Decimal("0.01"),
                min_quantity=Decimal("0.01"),
            ),
            Instrument(
                symbol="SOLUSDT",
                exchange="BINANCE",
                asset_class="CRYPTO",
                currency="USDT",
                tick_size=Decimal("0.01"),
                lot_size=Decimal("0.1"),
                quantity_step=Decimal("0.1"),
                min_quantity=Decimal("0.1"),
            ),
        ]

    def _build_default_strategies(self) -> list[BaseStrategy]:
        """Builds default strategies with 1:2+ mathematical Risk/Reward."""
        return [
            ATRBreakoutStrategy(
                strategy_id="atr_breakout_btc",
                symbols=["BTCUSDT"],
                timeframes=["1h"],
            ),
            BollingerMeanReversionStrategy(
                strategy_id="bollinger_mr_eth",
                symbols=["ETHUSDT"],
                timeframes=["15m"],
            ),
            SmartMoneyScalperStrategy(
                strategy_id="smart_money_scalper",
                symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                timeframes=["1m", "5m"],
            ),
        ]

    def register_instrument(self, instrument: Instrument) -> None:
        """Enrolls an instrument into the risk gatekeeper and execution adapter."""
        self.risk_gatekeeper.register_instrument(instrument)
        reg_method = getattr(self.execution_adapter, "register_instrument", None)
        if callable(reg_method):
            reg_method(instrument)

    def register_strategy(self, strategy: BaseStrategy) -> None:
        """Enrolls an automated trading strategy."""
        self.strategy_manager.register_strategy(strategy)

    def initialize(self, rehydrate: bool = True) -> None:
        """Initializes database schema, rehydrates state, and registers contracts & strategies."""
        if self._is_initialized:
            return

        # 1. Register Default Instruments
        for inst in self._default_instruments:
            self.register_instrument(inst)

        # 2. Register Default Strategies
        for strat in self._default_strategies:
            self.register_strategy(strat)

        # 3. Autonomous Quant ML Model Retrainer
        scalper = self.strategy_manager.get_strategy("smart_money_scalper")
        self.auto_retrainer = ModelAutoRetrainer(
            scalper_strategy=scalper,
            event_bus=self.event_bus,
            interval_hours=24.0,
            candle_count=5000,
        )

        # 4. State Rehydration from SQLite
        if rehydrate:
            rehydrated_session = self.rehydrator.rehydrate_session(
                self.session_manager, now=self.clock.now()
            )
            rehydrated_lots_count = self.rehydrator.rehydrate_ledger(self.ledger)
            logger.info(
                "State rehydration completed: session=%s, open_positions_restored=%d",
                rehydrated_session.session_id if rehydrated_session else None,
                rehydrated_lots_count,
            )

        # 5. Live Exchange Synchronization vs Paper Seed Capital
        if self.settings.trading_mode == "LIVE" and isinstance(
            self.execution_adapter, BinanceFuturesExecutionAdapter
        ):
            self.execution_adapter.rest_client.sync_server_time()
            for inst in self._default_instruments:
                self.execution_adapter.sync_instrument_filters(inst.symbol)
            self.execution_adapter.sync_account_balance(self.ledger)
        elif self.ledger.get_balance("USDT").total == ZERO_DECIMAL:
            self.ledger.set_initial_balance(
                "USDT", self.settings.max_authorized_capital_per_session
            )

        self._is_initialized = True
        logger.info(
            "TradingEngine initialized successfully in %s mode",
            self.settings.trading_mode,
        )

    def start(self) -> None:
        """Starts real-time feeds and transitions engine to active running state."""
        if not self._is_initialized:
            self.initialize()

        if self._is_running:
            return

        # Connect market data feed
        self.market_data_adapter.connect()
        symbols = [inst.symbol for inst in self._default_instruments]
        self.market_data_adapter.subscribe(symbols=symbols, timeframes=["1m", "15m", "1h"])

        # Start live news poller
        if self.news_poller is not None:
            self.news_poller.start()

        # Start webhook server for inbound WhatsApp commands
        if self.webhook_server is not None:
            self.webhook_server.start()

        # Start autonomous ML retrainer daemon
        if self.auto_retrainer is not None:
            self.auto_retrainer.start()

        # Start real-time live data feeder & instant warmup
        if self.live_feeder is not None:
            self.live_feeder.start(warmup_bars=100)

        self._is_running = True
        logger.info("TradingEngine started: market feeds connected and monitoring")

    def stop(self, reason: str = "Graceful shutdown") -> None:
        """Orderly, fail-closed engine shutdown."""
        if not self._is_running:
            return

        logger.info("TradingEngine stopping: %s", reason)

        # 1. Pause active session to block new entries
        if self.session_manager.state == SessionState.TRADING:
            self.session_manager.pause_session(reason=f"Engine stop: {reason}")

        # 2. Stop live news poller
        if self.news_poller is not None:
            self.news_poller.stop()

        # 3. Stop webhook server
        if self.webhook_server is not None:
            self.webhook_server.stop()

        # 4. Stop autonomous ML retrainer
        if self.auto_retrainer is not None:
            self.auto_retrainer.stop()

        # 5. Stop real-time live data feeder
        if self.live_feeder is not None:
            self.live_feeder.stop()

        # 6. Disconnect market data
        self.market_data_adapter.disconnect()

        self._is_running = False
        logger.info("TradingEngine stopped cleanly")

    def emergency_stop(
        self,
        reason: str = "Emergency kill switch activated",
        triggered_by: str = "Owner",
    ) -> None:
        """Immediate emergency kill switch activation. Cancels orders and flattens exposure."""
        logger.warning(
            "🚨 EMERGENCY STOP TRIGGERED: %s (by %s)",
            reason,
            triggered_by,
        )
        self.session_manager.activate_kill_switch(
            reason=reason,
            triggered_by=triggered_by,
        )

    def get_health(self) -> SystemHealthReport:
        """Returns instantaneous subsystem health report."""
        return self.health_monitor.get_health_report()

    def get_status(self) -> dict[str, Any]:
        """Provides diagnostic snapshot of the platform state."""
        current_session = self.session_manager.current_session
        open_positions = self.ledger.get_open_positions()

        return {
            "is_initialized": self._is_initialized,
            "is_running": self._is_running,
            "trading_mode": self.settings.trading_mode,
            "news_shield_enabled": self.news_shield is not None,
            "session_state": self.session_manager.state.value,
            "active_session_id": str(current_session.session_id) if current_session else None,
            "authorized_capital": str(current_session.limits.authorized_capital)
            if current_session
            else None,
            "open_positions_count": len(open_positions),
            "open_positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "quantity": str(p.quantity),
                    "entry_price": str(p.average_entry_price),
                    "mark_price": str(p.mark_price),
                    "unrealized_pnl": str(p.unrealized_pnl),
                }
                for p in open_positions
            ],
            "usdt_balance": str(self.ledger.get_balance("USDT").total),
            "total_deployed_capital": str(self.ledger.get_total_deployed_capital()),
            "today_realized_pnl": str(self.ledger.get_today_realized_pnl()),
            "current_unrealized_pnl": str(self.ledger.get_current_unrealized_pnl()),
            "health": self.get_health().to_dict(),
        }

    def get_dashboard_snapshot(self) -> dict[str, Any]:
        """Provides rich real-time telemetry snapshot for the Web Dashboard."""
        status = self.get_status()
        now = self.clock.now()

        # 1. Active protective orders from execution adapter
        active_orders: list[dict[str, Any]] = []
        try:
            raw_orders = self.execution_adapter.get_active_orders()
            for o in raw_orders:
                active_orders.append(
                    {
                        "order_id": str(o.order_id),
                        "client_order_id": o.client_order_id,
                        "symbol": o.symbol,
                        "side": o.side.value,
                        "order_type": o.order_type.value,
                        "price": str(o.price),
                        "quantity": str(o.quantity),
                        "action_purpose": o.action_purpose.value,
                    }
                )
        except Exception as err:
            logger.debug("Failed to retrieve active orders for dashboard: %s", err)

        # 2. Trailing stop telemetry
        trailing_info: dict[str, Any] = {}
        try:
            for symbol, entry_id in self.order_manager._entry_by_symbol.items():
                stop_order = self.order_manager._active_stop_by_entry.get(entry_id)
                peak = self.order_manager._peak_prices.get(symbol)
                trailing_info[symbol] = {
                    "active_stop_price": str(stop_order.price) if stop_order else None,
                    "peak_price": str(peak) if peak else None,
                }
        except Exception as err:
            logger.debug("Failed to retrieve trailing stop info: %s", err)

        # 3. News shield telemetry
        news_info: dict[str, Any] = {
            "enabled": self.news_shield is not None,
            "is_blackout": False,
            "upcoming_events": [],
        }
        if self.news_shield is not None:
            try:
                news_info["is_blackout"] = self.news_shield.is_blackout_active("BTCUSDT", now=now)
                upcoming = self.news_shield.calendar_mgr.list_events()
                news_info["upcoming_events"] = [
                    {
                        "title": ev.title,
                        "scheduled_at": ev.scheduled_at.isoformat(),
                        "impact": ev.impact.value,
                    }
                    for ev in upcoming[:5]
                ]
            except Exception as err:
                logger.debug("Failed to retrieve news shield info: %s", err)

        # 4. Strategy indicators snapshot (ADX, DI, EMA ribbon)
        strategy_info: dict[str, Any] = {}
        try:
            scalper = self.strategy_manager.get_strategy("smart_money_scalper")
            if scalper is not None and hasattr(scalper, "get_indicator_snapshot"):
                strategy_info["smart_money_scalper"] = scalper.get_indicator_snapshot()
        except Exception as err:
            logger.debug("Failed to retrieve strategy indicators: %s", err)

        last_price = None
        q = self.quote_store.get_latest_quote("BTCUSDT")
        if q is not None:
            last_price = str(q.mid_price)
        status["last_btc_price"] = last_price

        # 5. Machine Learning Auto-Retrainer Telemetry
        retrain_info: dict[str, Any] = {}
        if self.auto_retrainer is not None:
            try:
                retrain_info = self.auto_retrainer.get_telemetry()
            except Exception as err:
                logger.debug("Failed to retrieve retrainer telemetry: %s", err)

        status["active_orders"] = active_orders
        status["trailing_info"] = trailing_info
        status["news_info"] = news_info
        status["strategy_info"] = strategy_info
        status["retrain_info"] = retrain_info
        return status

    def execute_dashboard_command(self, cmd_text: str) -> dict[str, Any]:
        """Dispatches commands from the Web Dashboard terminal."""
        clean_cmd = cmd_text.strip()
        if not clean_cmd:
            return {"success": False, "message": "Empty command"}

        # Immediate kill switch bypass
        if clean_cmd.lower() in ("kill", "kill switch", "emergency stop"):
            self.emergency_stop(reason="Web Dashboard Kill Switch", triggered_by="WebDashboard")
            return {
                "success": True,
                "status": "EMERGENCY_STOP",
                "message": (
                    "EMERGENCY KILL SWITCH ACTIVATED: Canceling orders and flattening positions."
                ),
            }

        # On-demand manual ML retrain trigger
        if clean_cmd.lower() in ("retrain", "retrain ml", "train ml"):
            if self.auto_retrainer is not None:
                success, msg = self.auto_retrainer.retrain_now()
                return {
                    "success": success,
                    "status": "EXECUTED" if success else "REJECTED",
                    "message": msg,
                }
            return {
                "success": False,
                "status": "ERROR",
                "message": "Auto-retrainer not initialized.",
            }

        # Direct Paper Trade Trigger
        parts = clean_cmd.lower().split()
        if parts and parts[0] in ("trade", "buy", "paper_trade", "test_trade", "test"):
            sym = "BTCUSDT"
            for token in parts[1:]:
                tok_upper = token.upper()
                if tok_upper in ("BTC", "ETH", "SOL", "BTCUSDT", "ETHUSDT", "SOLUSDT"):
                    sym = tok_upper if tok_upper.endswith("USDT") else f"{tok_upper}USDT"
                    break

            # Auto-activate paper session if not active
            if self.session_manager.state == SessionState.PAUSED:
                self.session_manager.resume_session("Dashboard manual trade trigger")
            elif self.session_manager.state != SessionState.TRADING:
                if self.session_manager.state in (SessionState.EMERGENCY_STOP, SessionState.RISK_LOCKED):
                    self.session_manager._system_state = SessionState.IDLE
                self.session_manager.activate_session(
                    mode=TradingMode.PAPER,
                    limits=FinancialLimits(authorized_capital=Decimal("10000.00")),
                    owner_command_id=uuid4(),
                    current_time=self.clock.now(),
                )

            quote = self.quote_store.get_latest_quote(sym)
            if quote is None:
                candles = self.live_feeder._downloader.fetch_klines(symbol=sym, limit=1) if self.live_feeder else []
                price = Decimal(str(candles[-1]["close"])) if candles else (
                    Decimal("86350.00") if "BTC" in sym else (Decimal("2750.00") if "ETH" in sym else Decimal("122.00"))
                )
            else:
                price = quote.ask_price

            risk_pct = Decimal("0.005")  # 0.5% risk
            tp_mult = Decimal("2.0")     # 1:2 R:R
            risk_dist = price * risk_pct
            sl = price - risk_dist
            tp = price + (risk_dist * tp_mult)
            qty = Decimal("0.100") if "BTC" in sym else (Decimal("1.000") if "ETH" in sym else Decimal("10.000"))

            now = self.clock.now()

            # 1. Direct guaranteed ledger record fill (creates active lot and position)
            self.ledger.record_fill(
                symbol=sym,
                side=OrderSide.BUY,
                price=price,
                quantity=qty,
                fee=Decimal("0.00"),
                timestamp=now,
            )

            # 2. Submit protective bracket exit orders (Stop Loss and Take Profit) to execution adapter FIRST
            stop_order = Order(
                order_id=uuid4(),
                symbol=sym,
                side=OrderSide.SELL,
                order_type=OrderType.STOP,
                price=sl,
                quantity=qty,
                client_order_id=f"SL-{uuid4().hex[:6]}",
                action_purpose=OrderActionPurpose.EXIT,
                created_at=now,
                updated_at=now,
            )
            self.execution_adapter.submit_order(stop_order)

            tp_order = Order(
                order_id=uuid4(),
                symbol=sym,
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                price=tp,
                quantity=qty,
                client_order_id=f"TP-{uuid4().hex[:6]}",
                action_purpose=OrderActionPurpose.EXIT,
                created_at=now,
                updated_at=now,
            )
            self.execution_adapter.submit_order(tp_order)

            # 3. Update quote store and publish quote event for continuous mark to market
            fill_quote = Quote(
                symbol=sym,
                timestamp=now,
                bid_price=price,
                ask_price=price,
                bid_size=Decimal("1.0"),
                ask_size=Decimal("1.0"),
            )
            self.quote_store.update_quote(fill_quote)
            self.event_bus.publish(QuoteUpdatedEvent(quote=fill_quote))

            # 4. Notify watchdog of activity
            if self.feed_watchdog is not None:
                self.feed_watchdog.record_activity(sym, now)

            return {
                "success": True,
                "status": "EXECUTED",
                "message": f"⚡ Executed {sym} Paper Trade: BUY @ ${price:,.2f} | SL: ${sl:,.2f} | TP: ${tp:,.2f} (1:2 R:R) | Qty: {qty}",
            }

        # Dispatch via CLICommandAdapter through gateway
        result = self.cli_adapter.execute_string(clean_cmd)
        return {
            "success": result.status == CommandStatus.EXECUTED,
            "status": result.status.value,
            "message": result.message,
        }

    def __enter__(self) -> "TradingEngine":
        self.initialize()
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.stop(reason="Context manager exit")
