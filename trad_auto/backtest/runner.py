"""Event-driven backtesting simulation runner."""

from collections import deque
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from math import ceil
from uuid import uuid4

from trad_auto.backtest.metrics import calculate_performance_metrics
from trad_auto.backtest.models import BacktestConfig, BacktestResult, EquityPoint, TradeRecord
from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide, OrderType, TradingMode
from trad_auto.core.events import (
    BarCompletedEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.market_data import Bar, Quote, Trade
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.time import SimulatedClock
from trad_auto.execution.order_manager import OrderManager
from trad_auto.execution.simulated_adapter import SimulatedExecutionAdapter
from trad_auto.market_data.store import BarStore
from trad_auto.portfolio.ledger import PositionLedger
from trad_auto.risk.gatekeeper import RiskGatekeeper
from trad_auto.risk.real_bridge import LedgerPortfolioRiskBridge
from trad_auto.strategies.base import BaseStrategy
from trad_auto.strategies.manager import StrategyManager


class _PendingLot:
    """Internal tracker for open lot entry details used for trade record construction."""

    def __init__(self, entry_price: Decimal, timestamp: datetime, quantity: Decimal) -> None:
        self.entry_price = entry_price
        self.timestamp = timestamp
        self.quantity = quantity


class BacktestRunner:
    """Orchestrates an event-driven backtesting simulation.

    Wires the complete platform pipeline:
    `Market Data -> SimulatedClock -> BarStore -> StrategyManager`
    `-> RiskGatekeeper -> OrderManager -> SimulatedExecutionAdapter -> PositionLedger`.

    Ensures zero look-ahead bias by advancing time monotonically and only
    evaluating strategies on closed candles (`BarCompletedEvent`).
    """

    def __init__(
        self,
        config: BacktestConfig,
        instrument: Instrument,
        strategy: BaseStrategy | Sequence[BaseStrategy],
        financial_limits: FinancialLimits | None = None,
        clock: SimulatedClock | None = None,
        order_type: OrderType = OrderType.MARKET,
    ) -> None:
        self.config = config
        self.instrument = instrument
        self.order_type = order_type

        # 1. Monotonic Simulated Clock Authority
        self.clock = clock or SimulatedClock(config.start_time)

        # 2. Central Event Dispatcher
        self.event_bus = EventBus()

        # 3. Session Manager
        self.session_manager = TradingSessionManager(event_bus=self.event_bus)

        # 4. Multi-Currency Double-Entry Ledger
        self.ledger = PositionLedger(
            event_bus=self.event_bus,
            clock=self.clock,
            default_quote_asset=config.default_quote_asset,
        )
        self.ledger.set_initial_balance(config.default_quote_asset, config.initial_cash)

        # 5. Risk Firewall & Dynamic Bridge
        self.risk_bridge = LedgerPortfolioRiskBridge(ledger=self.ledger)
        self.risk_gatekeeper = RiskGatekeeper(
            event_bus=self.event_bus,
            session_manager=self.session_manager,
            portfolio_bridge=self.risk_bridge,
            instruments={instrument.symbol: instrument},
            order_type=order_type,
        )

        # 6. Simulated Execution Venue with Fee Tiers & Slippage
        self.execution_adapter = SimulatedExecutionAdapter(
            event_bus=self.event_bus,
            clock=self.clock,
            maker_fee_rate=config.maker_fee_rate,
            taker_fee_rate=config.taker_fee_rate,
            slippage_rate=config.slippage_rate,
        )

        # 7. Order Manager coordinating brackets and OCO
        self.order_manager = OrderManager(
            event_bus=self.event_bus,
            adapter=self.execution_adapter,
            ledger=self.ledger,
            clock=self.clock,
        )

        # 8. High-Performance In-Memory Bar Store
        self.bar_store = BarStore(max_bars=2000)

        # 9. Strategy Manager & Registration
        self.strategy_manager = StrategyManager(
            event_bus=self.event_bus,
            bar_store=self.bar_store,
            session_state_provider=self.session_manager.get_state,
        )

        strategies_list = [strategy] if isinstance(strategy, BaseStrategy) else list(strategy)
        for s in strategies_list:
            self.strategy_manager.register_strategy(s)

        # 10. Trade & Equity Recording
        self._trades: list[TradeRecord] = []
        self._pending_lots: deque[_PendingLot] = deque()
        self._equity_curve: list[EquityPoint] = []
        self._peak_equity = config.initial_cash

        # Register event subscriptions for trade tracking
        self.event_bus.subscribe(PositionOpenedEvent, self._handle_position_opened)
        self.event_bus.subscribe(PositionClosedEvent, self._handle_position_closed)

        # 11. Authorize Backtest Session
        total_seconds = max((config.end_time - config.start_time).total_seconds(), 3600.0)
        duration_hours = int(ceil(total_seconds / 3600.0)) + 48
        limits = financial_limits or FinancialLimits(authorized_capital=config.initial_cash)
        self.session_manager.activate_session(
            mode=TradingMode.BACKTEST,
            limits=limits,
            owner_command_id=uuid4(),
            current_time=config.start_time,
            duration_hours=duration_hours,
        )

    def _handle_position_opened(self, event: PositionOpenedEvent) -> None:
        """Records entry price and timestamp for FIFO trade pairing."""
        self._pending_lots.append(
            _PendingLot(
                entry_price=event.entry_price,
                timestamp=event.timestamp,
                quantity=event.quantity,
            )
        )

    def _handle_position_closed(self, event: PositionClosedEvent) -> None:
        """Constructs a completed TradeRecord upon position exit."""
        entry_price = event.exit_price
        entry_time = event.timestamp

        if self._pending_lots:
            lot = self._pending_lots.popleft()
            entry_price = lot.entry_price
            entry_time = lot.timestamp

        cost_basis = entry_price * event.closed_quantity
        if cost_basis > ZERO_DECIMAL:
            pnl_pct = (event.realized_pnl / cost_basis) * Decimal("100.0")
        else:
            pnl_pct = ZERO_DECIMAL

        duration_sec = Decimal(str(max((event.timestamp - entry_time).total_seconds(), 0.0)))

        self._trades.append(
            TradeRecord(
                symbol=event.symbol,
                side=event.side,
                entry_price=entry_price,
                exit_price=event.exit_price,
                quantity=event.closed_quantity,
                realized_pnl=event.realized_pnl,
                pnl_pct=pnl_pct,
                fees=event.total_fees,
                entry_time=entry_time,
                exit_time=event.timestamp,
                holding_duration_seconds=duration_sec,
            )
        )

    def _snapshot_equity(self, timestamp: datetime) -> None:
        """Records an equity curve snapshot."""
        cash = self.ledger.get_balance(self.config.default_quote_asset).total
        unrealized = self.ledger.get_current_unrealized_pnl()
        realized = sum((t.realized_pnl for t in self._trades), ZERO_DECIMAL)
        total_equity = cash + unrealized

        if total_equity > self._peak_equity:
            self._peak_equity = total_equity

        drawdown = self._peak_equity - total_equity
        if self._peak_equity > ZERO_DECIMAL:
            drawdown_pct = (drawdown / self._peak_equity) * Decimal("100.0")
        else:
            drawdown_pct = ZERO_DECIMAL

        self._equity_curve.append(
            EquityPoint(
                timestamp=timestamp,
                cash=cash,
                unrealized_pnl=unrealized,
                realized_pnl=realized,
                total_equity=total_equity,
                drawdown=drawdown,
                drawdown_pct=drawdown_pct,
            )
        )

    def run(self, bars: Sequence[Bar]) -> BacktestResult:
        """Executes historical simulation across chronological bars.

        Args:
            bars: Chronologically sorted candle bars.

        Returns:
            BacktestResult with performance metrics, equity series, and trade logs.
        """
        if not bars:
            empty_metrics = calculate_performance_metrics(
                initial_cash=self.config.initial_cash,
                equity_curve=[],
                trades=[],
                start_time=self.config.start_time,
                end_time=self.config.end_time,
                risk_free_rate=self.config.risk_free_rate,
            )
            return BacktestResult(
                config=self.config,
                metrics=empty_metrics,
                equity_curve=[],
                trades=[],
            )

        # Replay bars chronologically
        for bar in bars:
            # 1. Advance clock to bar timestamp
            self.clock.set_time(bar.timestamp)

            # 2. Intrabar quote & high/low trade tick matching for resting limit/stop orders
            open_quote = Quote(
                symbol=bar.symbol,
                bid_price=bar.open,
                ask_price=bar.open,
                bid_size=Decimal("100"),
                ask_size=Decimal("100"),
                timestamp=bar.timestamp,
            )
            self.event_bus.publish(QuoteUpdatedEvent(quote=open_quote))

            # Simulate intrabar price action to evaluate resting bracket orders
            high_trade = Trade(
                trade_id=f"high-{uuid4().hex[:8]}",
                symbol=bar.symbol,
                price=bar.high,
                quantity=Decimal("1"),
                side=OrderSide.BUY,
                timestamp=bar.timestamp,
            )
            self.event_bus.publish(TradeReceivedEvent(trade=high_trade))

            low_trade = Trade(
                trade_id=f"low-{uuid4().hex[:8]}",
                symbol=bar.symbol,
                price=bar.low,
                quantity=Decimal("1"),
                side=OrderSide.SELL,
                timestamp=bar.timestamp,
            )
            self.event_bus.publish(TradeReceivedEvent(trade=low_trade))

            # Update mark price to candle close
            close_quote = Quote(
                symbol=bar.symbol,
                bid_price=bar.close,
                ask_price=bar.close,
                bid_size=Decimal("100"),
                ask_size=Decimal("100"),
                timestamp=bar.timestamp,
            )
            self.event_bus.publish(QuoteUpdatedEvent(quote=close_quote))

            # 3. Add bar to in-memory store and notify strategies
            self.bar_store.add_bar(bar)
            self.event_bus.publish(BarCompletedEvent(bar=bar))

            # 4. Snapshot equity after order processing and ledger marks
            self._snapshot_equity(bar.timestamp)

        # 5. Terminal Liquidation: flatten any remaining open lots at final close price
        if self.ledger.get_open_positions():
            self.order_manager.flatten_all_positions()
            final_time = bars[-1].timestamp
            self._snapshot_equity(final_time)

        # 7. Compute institutional performance metrics
        metrics = calculate_performance_metrics(
            initial_cash=self.config.initial_cash,
            equity_curve=self._equity_curve,
            trades=self._trades,
            start_time=self.config.start_time,
            end_time=self.config.end_time,
            risk_free_rate=self.config.risk_free_rate,
        )

        return BacktestResult(
            config=self.config,
            metrics=metrics,
            equity_curve=self._equity_curve,
            trades=self._trades,
        )
