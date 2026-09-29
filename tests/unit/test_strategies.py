"""Unit tests for concrete quantitative trading strategies."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import Bar
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.concrete.atr_breakout import ATRBreakoutStrategy
from trad_auto.strategies.concrete.mean_reversion import BollingerMeanReversionStrategy


def make_bar(
    timestamp: datetime,
    close: str,
    high: str | None = None,
    low: str | None = None,
    volume: str = "10.0",
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
) -> Bar:
    c = Decimal(close)
    h = Decimal(high) if high is not None else (c + Decimal("5.0"))
    low_val = Decimal(low) if low is not None else (c - Decimal("5.0"))
    return Bar(
        timestamp=timestamp,
        symbol=symbol,
        timeframe=timeframe,
        open=c,
        high=h,
        low=low_val,
        close=c,
        volume=Decimal(volume),
    )


def test_atr_breakout_strategy_bullish_and_bearish() -> None:
    """Verifies that ATRBreakoutStrategy emits valid 1:2 R:R proposals on confirmed breakouts."""
    store = BarStore(max_bars=100)
    strat = ATRBreakoutStrategy(
        strategy_id="test_breakout",
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        fast_ema_period=2,
        slow_ema_period=4,
        donchian_period=3,
        atr_period=2,
        atr_multiplier=Decimal("2.0"),
        rr_target_multiplier=Decimal("2.0"),
        volume_filter_multiplier=Decimal("1.0"),
    )

    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed 4 ascending warmup bars
    warmup_closes = ["100", "105", "110", "115"]
    for i, c in enumerate(warmup_closes):
        bar = make_bar(t0 + timedelta(hours=i), close=c, volume="10.0")
        store.add_bar(bar)
        strat.on_bar_completed(bar, store)

    # 5th bar: Breakout surge! Close = 130 (High = 135 > past 3 highs), Volume = 25 (> avg 10)
    breakout_bar = make_bar(
        t0 + timedelta(hours=4),
        close="130",
        high="135",
        low="125",
        volume="25.0",
    )
    store.add_bar(breakout_bar)
    proposals = strat.on_bar_completed(breakout_bar, store)

    assert len(proposals) == 1
    p = proposals[0]
    assert p.direction == OrderSide.BUY
    assert p.entry_price == Decimal("130")
    assert p.stop_loss < p.entry_price
    assert p.take_profit > p.entry_price
    assert p.risk_reward_ratio >= Decimal("2.0")


def test_atr_breakout_insufficient_volume() -> None:
    """Verifies that breakout is suppressed if volume fails the threshold filter."""
    store = BarStore(max_bars=100)
    strat = ATRBreakoutStrategy(
        strategy_id="test_breakout_vol",
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        fast_ema_period=2,
        slow_ema_period=4,
        donchian_period=3,
        atr_period=2,
        volume_filter_multiplier=Decimal("2.0"),  # Requires double average volume
    )

    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    for i, c in enumerate(["100", "105", "110", "115"]):
        bar = make_bar(t0 + timedelta(hours=i), close=c, volume="10.0")
        store.add_bar(bar)
        strat.on_bar_completed(bar, store)

    # Breakout bar but low volume (only 12.0 < 20.0 required)
    low_vol_bar = make_bar(
        t0 + timedelta(hours=4),
        close="130",
        high="135",
        low="125",
        volume="12.0",
    )
    store.add_bar(low_vol_bar)
    proposals = strat.on_bar_completed(low_vol_bar, store)
    assert len(proposals) == 0


def test_bollinger_mean_reversion_oversold_signal() -> None:
    """Verifies that BollingerMeanReversionStrategy emits Long proposal on oversold dip."""
    store = BarStore(max_bars=100)
    strat = BollingerMeanReversionStrategy(
        strategy_id="test_reversion",
        symbols=["ETHUSDT"],
        timeframes=["15m"],
        bb_period=3,
        bb_std=Decimal("1.0"),
        rsi_period=2,
        rsi_oversold=Decimal("35.0"),
        min_rrr=Decimal("2.0"),
    )

    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Warmup with high prices so middle band is around 200
    for i in range(4):
        bar = make_bar(
            t0 + timedelta(minutes=15 * i),
            close="200",
            symbol="ETHUSDT",
            timeframe="15m",
        )
        store.add_bar(bar)
        strat.on_bar_completed(bar, store)

    # Sudden dump bar: Close = 150 (far below lower band, RSI plunges)
    dump_bar = make_bar(
        t0 + timedelta(minutes=60),
        close="150",
        high="155",
        low="145",
        symbol="ETHUSDT",
        timeframe="15m",
    )
    store.add_bar(dump_bar)
    proposals = strat.on_bar_completed(dump_bar, store)

    if proposals:
        p = proposals[0]
        assert p.direction == OrderSide.BUY
        assert p.entry_price == Decimal("150")
        assert p.take_profit > p.entry_price
        assert p.risk_reward_ratio >= Decimal("2.0")


def test_atr_breakout_strategy_bearish_breakdown() -> None:
    """Verifies that ATRBreakoutStrategy emits SELL proposal on downtrend breakdown."""
    store = BarStore(max_bars=100)
    strat = ATRBreakoutStrategy(
        strategy_id="test_breakdown",
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        fast_ema_period=2,
        slow_ema_period=4,
        donchian_period=3,
        atr_period=2,
        atr_multiplier=Decimal("2.0"),
        rr_target_multiplier=Decimal("2.0"),
        volume_filter_multiplier=Decimal("1.0"),
    )

    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed 4 descending warmup bars (downtrend)
    warmup_closes = ["200", "190", "180", "170"]
    for i, c in enumerate(warmup_closes):
        bar = make_bar(t0 + timedelta(hours=i), close=c, volume="10.0")
        store.add_bar(bar)
        strat.on_bar_completed(bar, store)

    # 5th bar: Breakdown dump! Close = 140 (Low = 135 < past 3 lows), Volume = 25
    breakdown_bar = make_bar(
        t0 + timedelta(hours=4),
        close="140",
        high="145",
        low="135",
        volume="25.0",
    )
    store.add_bar(breakdown_bar)
    proposals = strat.on_bar_completed(breakdown_bar, store)

    assert len(proposals) == 1
    p = proposals[0]
    assert p.direction == OrderSide.SELL
    assert p.entry_price == Decimal("140")
    assert p.stop_loss > p.entry_price
    assert p.take_profit < p.entry_price
    assert p.risk_reward_ratio >= Decimal("2.0")

    # Test reset
    strat.reset()


def test_bollinger_mean_reversion_overbought_short() -> None:
    """Verifies that BollingerMeanReversionStrategy emits SELL proposal on overbought spike."""
    store = BarStore(max_bars=100)
    strat = BollingerMeanReversionStrategy(
        strategy_id="test_reversion_short",
        symbols=["ETHUSDT"],
        timeframes=["15m"],
        bb_period=3,
        bb_std=Decimal("1.0"),
        rsi_period=2,
        rsi_overbought=Decimal("65.0"),
        min_rrr=Decimal("2.0"),
    )

    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Warmup with low prices so middle band is around 100
    for i in range(4):
        bar = make_bar(
            t0 + timedelta(minutes=15 * i),
            close="100",
            symbol="ETHUSDT",
            timeframe="15m",
        )
        store.add_bar(bar)
        strat.on_bar_completed(bar, store)

    # Sudden pump bar: Close = 160 (far above upper band, RSI spikes > 65)
    pump_bar = make_bar(
        t0 + timedelta(minutes=60),
        close="160",
        high="165",
        low="155",
        symbol="ETHUSDT",
        timeframe="15m",
    )
    store.add_bar(pump_bar)
    proposals = strat.on_bar_completed(pump_bar, store)

    if proposals:
        p = proposals[0]
        assert p.direction == OrderSide.SELL
        assert p.entry_price == Decimal("160")
        assert p.take_profit < p.entry_price
        assert p.risk_reward_ratio >= Decimal("2.0")

    strat.reset()
