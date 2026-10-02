"""Unit tests for SmartMoneyScalperStrategy."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from trad_auto.ai.smart_money import BinanceSmartMoneyAnalyzer
from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import Bar
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.concrete.smart_money_scalper import SmartMoneyScalperStrategy


def _make_bar(
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    close: Decimal,
    volume: Decimal = Decimal("100.0"),
    high: Decimal | None = None,
    low: Decimal | None = None,
    open_: Decimal | None = None,
) -> Bar:
    c = close
    hi = high or (c + Decimal("10.0"))
    lo = low or (c - Decimal("10.0"))
    o = open_ or c
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        open=o,
        high=hi,
        low=lo,
        close=c,
        volume=volume,
        timestamp=timestamp,
    )


def test_smart_money_scalper_bullish_long_setup() -> None:
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    mock_analyzer.validate_proposal_alignment.return_value = (True, "Whales aligned 65% Long")

    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_test",
        symbols=["BTCUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        atr_multiplier=Decimal("1.5"),
        rr_target_multiplier=Decimal("2.0"),
        volume_multiplier=Decimal("1.0"),
        rsi_long_upper=Decimal("100.0"),
        smart_money_analyzer=mock_analyzer,
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed rising sequence to warm up EMA (Fast > Slow > Trend) and establish RSI in [52, 70]
    base_price = Decimal("50000.0")
    for i in range(25):
        t = now + timedelta(minutes=i)
        price = base_price + Decimal(str(i * 15))
        vol = Decimal("50.0") if i < 24 else Decimal("100.0")  # Volume surge on trigger bar
        bar = _make_bar("BTCUSDT", "1m", t, close=price, volume=vol)
        store.add_bar(bar)
        proposals = strategy.on_bar_completed(bar, store)

    # Last bar should emit a LONG proposal with 1:2 R:R
    assert len(proposals) >= 1
    proposal = proposals[-1]
    assert proposal.direction == OrderSide.BUY
    assert proposal.symbol == "BTCUSDT"
    assert proposal.timeframe == "1m"
    assert proposal.entry_price > proposal.stop_loss
    assert proposal.take_profit > proposal.entry_price

    # Verify mathematical 1:2 Risk/Reward
    risk = proposal.entry_price - proposal.stop_loss
    reward = proposal.take_profit - proposal.entry_price
    assert (reward / risk) >= Decimal("1.99")
    assert "Whales aligned 65% Long" in proposal.reason


def test_smart_money_scalper_blocks_on_whale_divergence() -> None:
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    # Whales oppose the long trade!
    mock_analyzer.validate_proposal_alignment.return_value = (
        False,
        "Blocked by Smart Money Shield: Whales are 2.5x net SHORT",
    )

    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_test",
        symbols=["BTCUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        volume_multiplier=Decimal("1.0"),
        rsi_long_upper=Decimal("100.0"),
        smart_money_analyzer=mock_analyzer,
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    base_price = Decimal("50000.0")
    for i in range(25):
        t = now + timedelta(minutes=i)
        price = base_price + Decimal(str(i * 15))
        vol = Decimal("100.0")
        bar = _make_bar("BTCUSDT", "1m", t, close=price, volume=vol)
        store.add_bar(bar)
        proposals = strategy.on_bar_completed(bar, store)

    # Proposals must be blocked because whales oppose
    assert len(proposals) == 0


def test_smart_money_scalper_blocks_insufficient_volume() -> None:
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    mock_analyzer.validate_proposal_alignment.return_value = (True, "Aligned")

    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_test",
        symbols=["BTCUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        volume_multiplier=Decimal("2.0"),  # Requires double average volume
        rsi_long_upper=Decimal("100.0"),
        smart_money_analyzer=mock_analyzer,
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    base_price = Decimal("50000.0")
    for i in range(25):
        t = now + timedelta(minutes=i)
        price = base_price + Decimal(str(i * 15))
        bar = _make_bar("BTCUSDT", "1m", t, close=price, volume=Decimal("10.0"))  # Low volume
        store.add_bar(bar)
        proposals = strategy.on_bar_completed(bar, store)

    assert len(proposals) == 0


def test_smart_money_scalper_bearish_short_setup() -> None:
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    mock_analyzer.validate_proposal_alignment.return_value = (True, "Whales aligned 70% Short")

    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_test",
        symbols=["BTCUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        atr_multiplier=Decimal("1.5"),
        rr_target_multiplier=Decimal("2.0"),
        volume_multiplier=Decimal("1.0"),
        rsi_short_lower=Decimal("0.0"),
        smart_money_analyzer=mock_analyzer,
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed falling sequence for short signal (red candles: open > close)
    base_price = Decimal("50000.0")
    for i in range(25):
        t = now + timedelta(minutes=i)
        price = base_price - Decimal(str(i * 15))
        vol = Decimal("50.0") if i < 24 else Decimal("100.0")
        bar = _make_bar(
            "BTCUSDT",
            "1m",
            t,
            close=price,
            open_=price + Decimal("5.0"),
            volume=vol,
        )
        store.add_bar(bar)
        proposals = strategy.on_bar_completed(bar, store)

    assert len(proposals) >= 1
    proposal = proposals[-1]
    assert proposal.direction == OrderSide.SELL
    assert proposal.entry_price < proposal.stop_loss
    assert proposal.take_profit < proposal.entry_price
    risk = proposal.stop_loss - proposal.entry_price
    reward = proposal.entry_price - proposal.take_profit
    assert (reward / risk) >= Decimal("1.99")


def test_smart_money_scalper_blocks_on_qlib_alpha_rejection() -> None:
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    mock_analyzer.validate_proposal_alignment.return_value = (True, "Whales aligned Long")

    # Mock Qlib alpha engine returning a negative conviction score for a Long setup
    mock_qlib = MagicMock()
    mock_qlib.lookback_bars = 10
    mock_snapshot = MagicMock()
    mock_snapshot.composite_score = Decimal("-0.40")  # Bearish flow opposing Long
    mock_qlib.calculate_alpha.return_value = mock_snapshot

    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_test",
        symbols=["BTCUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        volume_multiplier=Decimal("1.0"),
        rsi_long_upper=Decimal("100.0"),
        smart_money_analyzer=mock_analyzer,
        qlib_alpha_engine=mock_qlib,
        enable_qlib_alpha=True,
        qlib_min_conviction=Decimal("0.30"),
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    base_price = Decimal("50000.0")
    for i in range(25):
        t = now + timedelta(minutes=i)
        price = base_price + Decimal(str(i * 15))
        bar = _make_bar("BTCUSDT", "1m", t, close=price, volume=Decimal("100.0"))
        store.add_bar(bar)
        proposals = strategy.on_bar_completed(bar, store)

    # Must be blocked because Qlib composite score (-0.40) < threshold (0.30)
    assert len(proposals) == 0


def test_smart_money_scalper_reset_clears_indicators() -> None:
    strategy = SmartMoneyScalperStrategy(symbols=["BTCUSDT"], timeframes=["1m"])
    bar = _make_bar("BTCUSDT", "1m", datetime(2026, 1, 1, 10, 0, tzinfo=UTC), Decimal("50000"))
    store = BarStore()
    strategy.on_bar_completed(bar, store)

    strategy.reset()
    key = ("BTCUSDT", "1m")
    assert strategy._fast_emas[key].is_ready is False
    assert strategy._rsis[key].is_ready is False
    assert strategy._adxs[key].is_ready is False


def test_smart_money_scalper_blocks_on_adx_chop() -> None:
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    mock_analyzer.validate_proposal_alignment.return_value = (True, "Whales aligned Long")

    # Fast ADX period (5) with high threshold (25.0) to test chop block
    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_test",
        symbols=["BTCUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        volume_multiplier=Decimal("1.0"),
        rsi_long_upper=Decimal("100.0"),
        smart_money_analyzer=mock_analyzer,
        adx_period=5,
        adx_threshold=Decimal("25.0"),
        enable_adx_filter=True,
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed 20 tight oscillating bars where ADX will stay well below 25.0
    for i in range(20):
        t = now + timedelta(minutes=i)
        p = Decimal("50000.0") if i % 2 == 0 else Decimal("50005.0")
        bar = _make_bar("BTCUSDT", "1m", t, close=p, volume=Decimal("100.0"))
        store.add_bar(bar)
        proposals = strategy.on_bar_completed(bar, store)

    # Any signal in chop must be rejected by ADX regime filter
    assert len(proposals) == 0


def test_smart_money_scalper_cross_sectional_ranking_multi_asset() -> None:
    """Verifies that cross-sectional ranking filters out lower-ranked assets."""
    mock_analyzer = MagicMock(spec=BinanceSmartMoneyAnalyzer)
    mock_analyzer.validate_proposal_alignment.return_value = (True, "Whales aligned Long")

    mock_ranker = MagicMock()
    mock_snapshot = MagicMock()
    mock_snapshot.top_asset = "ETHUSDT"  # ETH is top asset
    mock_snapshot.spread = 1.85
    mock_snapshot.dispersion = 0.45
    mock_snapshot.regime_label = "dispersed"
    mock_snapshot.n_tradeable = 2
    mock_ranker.rank_assets.return_value = mock_snapshot
    mock_ranker.should_trade.return_value = (True, "Favorable")

    strategy = SmartMoneyScalperStrategy(
        strategy_id="scalper_multi_test",
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["1m"],
        fast_ema_period=3,
        slow_ema_period=5,
        trend_ema_period=10,
        rsi_period=5,
        atr_period=5,
        volume_multiplier=Decimal("1.0"),
        rsi_long_upper=Decimal("100.0"),
        smart_money_analyzer=mock_analyzer,
        enable_cross_sectional=True,
        cross_sectional_ranker=mock_ranker,
        enable_ml_filter=False,
    )

    store = BarStore()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Feed 65 bars for both BTCUSDT and ETHUSDT
    proposals = []
    for i in range(65):
        t = now + timedelta(minutes=i)
        p_btc = Decimal("50000.0") + Decimal(str(i * 20))
        p_eth = Decimal("3000.0") + Decimal(str(i * 5))
        bar_btc = _make_bar("BTCUSDT", "1m", t, close=p_btc, volume=Decimal("100.0"))
        bar_eth = _make_bar("ETHUSDT", "1m", t, close=p_eth, volume=Decimal("100.0"))
        store.add_bar(bar_btc)
        store.add_bar(bar_eth)
        proposals = strategy.on_bar_completed(bar_btc, store)

    # When BTC bar is completed, because ETH is top_asset, BTC trade must be blocked!
    assert len(proposals) == 0  # Blocked because BTC != top_asset (ETH)

    # Check indicator snapshot includes cross-sectional telemetry
    snap = strategy.get_indicator_snapshot("BTCUSDT", "1m")
    assert snap["cs_top_asset"] == "ETHUSDT"
    assert snap["cs_regime"] == "dispersed"
    assert snap["cs_tradeable_count"] == 2



