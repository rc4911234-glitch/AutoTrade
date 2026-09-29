"""Integration and unit tests for event-driven BacktestRunner."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trad_auto.backtest.models import BacktestConfig
from trad_auto.backtest.runner import BacktestRunner
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide, SessionState
from trad_auto.core.models.instrument import Instrument
from trad_auto.core.models.market_data import Bar
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.base import BaseStrategy
from trad_auto.strategies.concrete.atr_breakout import ATRBreakoutStrategy


def _create_instrument() -> Instrument:
    return Instrument(
        symbol="BTCUSDT",
        exchange="BINANCE",
        asset_class="CRYPTO",
        currency="USDT",
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
    )


def _make_bar(
    symbol: str,
    timestamp: datetime,
    open_p: Decimal,
    high_p: Decimal,
    low_p: Decimal,
    close_p: Decimal,
    volume: Decimal = Decimal("100"),
) -> Bar:
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe="1h",
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=volume,
    )


class _DeterministicMockStrategy(BaseStrategy):
    """Mock strategy that generates programmed proposals on designated bar indices."""

    def __init__(self, proposal_triggers: dict[int, TradeProposal]) -> None:
        super().__init__("mock_strat", ["BTCUSDT"], ["1h"])
        self.proposal_triggers = proposal_triggers
        self.bar_count = 0

    def on_bar_completed(self, bar: Bar, bar_store: BarStore) -> list[TradeProposal]:
        idx = self.bar_count
        self.bar_count += 1
        if idx in self.proposal_triggers:
            return [self.proposal_triggers[idx]]
        return []

    def reset(self) -> None:
        self.bar_count = 0


class TestBacktestRunnerEmptyAndEdgeCases:
    """Edge case tests."""

    def test_empty_bars_returns_initial_cash(self) -> None:
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 10, tzinfo=UTC)
        config = BacktestConfig(
            symbol="BTCUSDT",
            start_time=start,
            end_time=end,
            initial_cash=Decimal("10000.00"),
        )
        instrument = _create_instrument()
        strategy = _DeterministicMockStrategy({})
        runner = BacktestRunner(config=config, instrument=instrument, strategy=strategy)

        result = runner.run([])
        assert result.metrics.total_trades == 0
        assert result.metrics.total_return_pct == ZERO_DECIMAL
        assert len(result.equity_curve) == 0


class TestBacktestRunnerMockLifecycle:
    """Full lifecycle verification: entry, bracket placement, OCO, and settlement."""

    def test_profitable_trade_take_profit_hit(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
        config = BacktestConfig(
            symbol="BTCUSDT",
            start_time=start,
            end_time=end,
            initial_cash=Decimal("10000.00"),
            maker_fee_rate=ZERO_DECIMAL,
            taker_fee_rate=ZERO_DECIMAL,
            slippage_rate=ZERO_DECIMAL,
        )
        instrument = _create_instrument()

        # Proposal on Bar 0: Long at 100, Stop 90, TP 120 (1:2 R:R)
        proposal = TradeProposal(
            proposal_id=uuid4(),
            strategy_id="mock_strat",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100.00"),
            stop_loss=Decimal("90.00"),
            take_profit=Decimal("120.00"),
            timestamp=start + timedelta(hours=1),
            reason="Mock breakout",
        )
        strategy = _DeterministicMockStrategy({0: proposal})
        runner = BacktestRunner(config=config, instrument=instrument, strategy=strategy)

        # Bar 0: Entry candle (Close 100) -> Entry order fills
        # Bar 1: Price rallies to High 125, Low 105, Close 122 -> Take Profit (120) hits!
        bars = [
            _make_bar(
                "BTCUSDT", start, Decimal("98"), Decimal("102"), Decimal("97"), Decimal("100")
            ),
            _make_bar(
                "BTCUSDT",
                start + timedelta(hours=1),
                Decimal("106"),
                Decimal("125"),
                Decimal("105"),
                Decimal("122"),
            ),
        ]

        result = runner.run(bars)

        assert result.metrics.total_trades == 1
        assert result.metrics.winning_trades == 1
        assert result.metrics.losing_trades == 0
        assert result.metrics.profit_factor == Decimal("999.99")
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.realized_pnl > ZERO_DECIMAL
        assert trade.exit_price == Decimal("120.00")

    def test_loss_trade_stop_loss_hit(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
        config = BacktestConfig(
            symbol="BTCUSDT",
            start_time=start,
            end_time=end,
            initial_cash=Decimal("10000.00"),
            maker_fee_rate=ZERO_DECIMAL,
            taker_fee_rate=ZERO_DECIMAL,
            slippage_rate=ZERO_DECIMAL,
        )
        instrument = _create_instrument()

        proposal = TradeProposal(
            proposal_id=uuid4(),
            strategy_id="mock_strat",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100.00"),
            stop_loss=Decimal("90.00"),
            take_profit=Decimal("120.00"),
            timestamp=start + timedelta(hours=1),
            reason="Mock breakout",
        )
        strategy = _DeterministicMockStrategy({0: proposal})
        runner = BacktestRunner(config=config, instrument=instrument, strategy=strategy)

        # Bar 0: Entry candle -> fills at 100
        # Bar 1: Drops to Low 88 -> Stop loss (90) hits!
        bars = [
            _make_bar(
                "BTCUSDT", start, Decimal("98"), Decimal("102"), Decimal("97"), Decimal("100")
            ),
            _make_bar(
                "BTCUSDT",
                start + timedelta(hours=1),
                Decimal("99"),
                Decimal("101"),
                Decimal("88"),
                Decimal("89"),
            ),
        ]

        result = runner.run(bars)

        assert result.metrics.total_trades == 1
        assert result.metrics.winning_trades == 0
        assert result.metrics.losing_trades == 1
        assert result.metrics.profit_factor == ZERO_DECIMAL
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.realized_pnl < ZERO_DECIMAL
        assert trade.exit_price <= Decimal("90.00")

    def test_terminal_liquidation_flattens_open_position(self) -> None:
        """Position left open when simulation ends is flattened at final bar close."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
        config = BacktestConfig(
            symbol="BTCUSDT",
            start_time=start,
            end_time=end,
            initial_cash=Decimal("10000.00"),
            maker_fee_rate=ZERO_DECIMAL,
            taker_fee_rate=ZERO_DECIMAL,
            slippage_rate=ZERO_DECIMAL,
        )
        instrument = _create_instrument()

        # Proposal on Bar 0: Entry 100, Stop 50, TP 200 (wide brackets that won't trigger)
        proposal = TradeProposal(
            proposal_id=uuid4(),
            strategy_id="mock_strat",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100.00"),
            stop_loss=Decimal("50.00"),
            take_profit=Decimal("200.00"),
            timestamp=start + timedelta(hours=1),
            reason="Mock wide trade",
        )
        strategy = _DeterministicMockStrategy({0: proposal})
        runner = BacktestRunner(config=config, instrument=instrument, strategy=strategy)

        # Bar 0: Enters at 100
        # Bar 1: Closes at 110 (within 50-200 bracket, so no bracket triggers)
        bars = [
            _make_bar(
                "BTCUSDT", start, Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100")
            ),
            _make_bar(
                "BTCUSDT",
                start + timedelta(hours=1),
                Decimal("101"),
                Decimal("112"),
                Decimal("99"),
                Decimal("110"),
            ),
        ]

        result = runner.run(bars)

        # Terminal liquidation should have flattened the open lot at final close price 110
        assert runner.ledger.get_open_positions_count() == 0
        assert result.metrics.total_trades == 1
        trade = result.trades[0]
        assert trade.realized_pnl > ZERO_DECIMAL
        assert trade.exit_price == Decimal("110.00")

    def test_risk_limit_breaker_halts_trading(self) -> None:
        """Max allowed loss triggers RISK_LOCKED and halts subsequent trade entries."""
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
        config = BacktestConfig(
            symbol="BTCUSDT",
            start_time=start,
            end_time=end,
            initial_cash=Decimal("10000.00"),
            maker_fee_rate=ZERO_DECIMAL,
            taker_fee_rate=ZERO_DECIMAL,
            slippage_rate=ZERO_DECIMAL,
        )
        instrument = _create_instrument()

        # Proposal 1 on Bar 0: Loss of ~100
        p1 = TradeProposal(
            proposal_id=uuid4(),
            strategy_id="mock_strat",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("100.00"),
            stop_loss=Decimal("90.00"),
            take_profit=Decimal("120.00"),
            timestamp=start + timedelta(hours=1),
            reason="Mock loss trade",
        )
        # Proposal 2 on Bar 2: Should be rejected because daily loss limit was breached
        p2 = TradeProposal(
            proposal_id=uuid4(),
            strategy_id="mock_strat",
            symbol="BTCUSDT",
            timeframe="1h",
            direction=OrderSide.BUY,
            entry_price=Decimal("90.00"),
            stop_loss=Decimal("80.00"),
            take_profit=Decimal("110.00"),
            timestamp=start + timedelta(hours=3),
            reason="Mock second trade",
        )

        strategy = _DeterministicMockStrategy({0: p1, 2: p2})
        # Set tight max_allowed_loss of 50.00
        limits = FinancialLimits(
            authorized_capital=Decimal("10000.00"),
            max_allowed_loss=Decimal("50.00"),
        )
        runner = BacktestRunner(
            config=config,
            instrument=instrument,
            strategy=strategy,
            financial_limits=limits,
        )

        bars = [
            # Bar 0: Enter at 100
            _make_bar(
                "BTCUSDT", start, Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100")
            ),
            # Bar 1: Stop hit at 90 (breaches 50.00 max loss!)
            _make_bar(
                "BTCUSDT",
                start + timedelta(hours=1),
                Decimal("98"),
                Decimal("99"),
                Decimal("85"),
                Decimal("88"),
            ),
            # Bar 2: Strategy tries p2, but system is now in RISK_LOCKED state!
            _make_bar(
                "BTCUSDT",
                start + timedelta(hours=2),
                Decimal("88"),
                Decimal("92"),
                Decimal("87"),
                Decimal("90"),
            ),
        ]

        result = runner.run(bars)

        # Only 1 trade executed, p2 was rejected
        assert runner.session_manager.state == SessionState.RISK_LOCKED
        assert result.metrics.total_trades == 1


class TestBacktestRunnerConcreteStrategyIntegration:
    """Integration test with actual ATRBreakoutStrategy."""

    def test_atr_breakout_strategy_end_to_end(self) -> None:
        start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 2, 12, 0, tzinfo=UTC)
        config = BacktestConfig(
            symbol="BTCUSDT",
            start_time=start,
            end_time=end,
            initial_cash=Decimal("10000.00"),
        )
        instrument = _create_instrument()
        strategy = ATRBreakoutStrategy(
            strategy_id="atr_test",
            symbols=["BTCUSDT"],
            timeframes=["1h"],
            fast_ema_period=3,
            slow_ema_period=5,
            donchian_period=3,
            atr_period=3,
        )
        runner = BacktestRunner(config=config, instrument=instrument, strategy=strategy)

        # Generate 25 rising bars to warm up indicators and trigger a breakout
        bars: list[Bar] = []
        base_price = Decimal("40000")
        for i in range(25):
            t = start + timedelta(hours=i)
            # Upward trending prices
            open_p = base_price + Decimal(str(i * 100))
            high_p = open_p + Decimal("150")
            low_p = open_p - Decimal("50")
            close_p = open_p + Decimal("120")
            bars.append(
                _make_bar("BTCUSDT", t, open_p, high_p, low_p, close_p, volume=Decimal("500"))
            )

        result = runner.run(bars)

        assert len(result.equity_curve) >= 25
        report = result.formatted_summary()
        assert "TRAD-AUTO BACKTEST REPORT" in report
        assert "BTCUSDT" in report
