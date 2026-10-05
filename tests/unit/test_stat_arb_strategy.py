"""Unit tests for StatisticalArbitrageStrategy."""

from datetime import UTC, datetime
from decimal import Decimal
import numpy as np
import pytest

from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import Bar
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.concrete.statistical_arbitrage import StatisticalArbitrageStrategy


@pytest.fixture
def bar_store() -> BarStore:
    return BarStore()


def _generate_cointegrated_bars(
    count: int = 50,
    divergence_bias: float = 0.0,
) -> tuple[list[Bar], list[Bar]]:
    """Generates synthetic cointegrated BTC and ETH price bars."""
    np.random.seed(42)
    btc_base = 60000.0
    beta = 0.045
    alpha = 100.0

    btc_prices = [btc_base]
    for _ in range(count - 1):
        btc_prices.append(btc_prices[-1] * (1.0 + np.random.normal(0, 0.002)))

    # ETH = beta * BTC + alpha + stationary noise
    eth_prices = []
    for i, p_btc in enumerate(btc_prices):
        noise = np.random.normal(0, 5.0)
        # Add artificial divergence on the last few bars if requested
        if i >= count - 5:
            noise += divergence_bias
        eth_prices.append(beta * p_btc + alpha + noise)

    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    btc_bars = []
    eth_bars = []
    for i in range(count):
        t = now.replace(minute=i % 60)
        btc_bars.append(
            Bar(
                symbol="BTCUSDT",
                timeframe="1m",
                open=Decimal(str(round(btc_prices[i], 2))),
                high=Decimal(str(round(btc_prices[i] * 1.001, 2))),
                low=Decimal(str(round(btc_prices[i] * 0.999, 2))),
                close=Decimal(str(round(btc_prices[i], 2))),
                volume=Decimal("100.0"),
                timestamp=t,
            )
        )
        eth_bars.append(
            Bar(
                symbol="ETHUSDT",
                timeframe="1m",
                open=Decimal(str(round(eth_prices[i], 2))),
                high=Decimal(str(round(eth_prices[i] * 1.001, 2))),
                low=Decimal(str(round(eth_prices[i] * 0.999, 2))),
                close=Decimal(str(round(eth_prices[i], 2))),
                volume=Decimal("500.0"),
                timestamp=t,
            )
        )
    return btc_bars, eth_bars


def test_stat_arb_init() -> None:
    strategy = StatisticalArbitrageStrategy(
        strategy_id="test_stat_arb",
        symbol_x="BTCUSDT",
        symbol_y="ETHUSDT",
        timeframes=["1m", "5m"],
    )
    assert strategy.strategy_id == "test_stat_arb"
    assert "BTCUSDT" in strategy.symbols
    assert "ETHUSDT" in strategy.symbols
    assert strategy.handles("ETHUSDT", "1m") is True
    assert strategy.handles("SOLUSDT", "1m") is False


def test_stat_arb_warming_up(bar_store: BarStore) -> None:
    strategy = StatisticalArbitrageStrategy()
    # Feed only 5 bars (below min_bars_required 30)
    btc_bars, eth_bars = _generate_cointegrated_bars(count=5)
    for b in btc_bars:
        bar_store.add_bar(b)
    for b in eth_bars:
        bar_store.add_bar(b)

    proposals = strategy.on_bar_completed(eth_bars[-1], bar_store)
    assert len(proposals) == 0
    telemetry = strategy.get_telemetry()
    assert telemetry["status"] == "WARMING_UP"


def test_stat_arb_neutral_spread(bar_store: BarStore) -> None:
    strategy = StatisticalArbitrageStrategy()
    btc_bars, eth_bars = _generate_cointegrated_bars(count=50, divergence_bias=0.0)
    for b in btc_bars:
        bar_store.add_bar(b)
    for b in eth_bars:
        bar_store.add_bar(b)

    proposals = strategy.on_bar_completed(eth_bars[-1], bar_store)
    # With 0 divergence bias, spread should be within equilibrium, no trade emitted
    assert len(proposals) == 0
    telemetry = strategy.get_telemetry()
    assert telemetry["status"] == "ACTIVE"
    assert abs(telemetry["z_score"]) < 2.0


def test_stat_arb_long_spread_proposal(bar_store: BarStore) -> None:
    # Severe negative divergence bias -> ETH is underpriced -> Z < -2.0 -> LONG_SPREAD
    strategy = StatisticalArbitrageStrategy(z_entry_threshold=1.5)
    btc_bars, eth_bars = _generate_cointegrated_bars(count=50, divergence_bias=-40.0)
    for b in btc_bars:
        bar_store.add_bar(b)
    for b in eth_bars:
        bar_store.add_bar(b)

    proposals = strategy.on_bar_completed(eth_bars[-1], bar_store)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.symbol == "ETHUSDT"
    assert p.direction == OrderSide.BUY
    assert p.stop_loss < p.entry_price < p.take_profit
    # Verify 1:2 Risk to Reward
    risk = p.entry_price - p.stop_loss
    reward = p.take_profit - p.entry_price
    assert reward >= risk * Decimal("2.0")


def test_stat_arb_short_spread_proposal(bar_store: BarStore) -> None:
    # Severe positive divergence bias -> ETH is overpriced -> Z > +2.0 -> SHORT_SPREAD
    strategy = StatisticalArbitrageStrategy(z_entry_threshold=1.5)
    btc_bars, eth_bars = _generate_cointegrated_bars(count=50, divergence_bias=40.0)
    for b in btc_bars:
        bar_store.add_bar(b)
    for b in eth_bars:
        bar_store.add_bar(b)

    proposals = strategy.on_bar_completed(eth_bars[-1], bar_store)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.symbol == "ETHUSDT"
    assert p.direction == OrderSide.SELL
    assert p.take_profit < p.entry_price < p.stop_loss
    risk = p.stop_loss - p.entry_price
    reward = p.entry_price - p.take_profit
    assert reward >= risk * Decimal("2.0")
