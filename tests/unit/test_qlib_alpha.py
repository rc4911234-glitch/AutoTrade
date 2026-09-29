"""Unit tests for Microsoft Qlib-inspired Alpha Microstructure Engine."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trad_auto.ai.qlib_alpha import QlibAlphaEngine, QlibAlphaSnapshot
from trad_auto.core.models.market_data import Bar


def _create_bar(
    symbol: str,
    timeframe: str,
    timestamp: datetime,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        timestamp=timestamp,
    )


def test_qlib_alpha_returns_none_if_insufficient_bars() -> None:
    engine = QlibAlphaEngine(lookback_bars=20)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    bars = [
        _create_bar(
            symbol="BTCUSDT",
            timeframe="1m",
            timestamp=now + timedelta(minutes=i),
            open_=Decimal("50000.0"),
            high=Decimal("50100.0"),
            low=Decimal("49900.0"),
            close=Decimal("50050.0"),
            volume=Decimal("100.0"),
        )
        for i in range(10)
    ]
    snapshot = engine.calculate_alpha(bars)
    assert snapshot is None


def test_qlib_alpha_bullish_conviction() -> None:
    engine = QlibAlphaEngine(lookback_bars=20, conviction_threshold=Decimal("0.50"))
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    bars: list[Bar] = []

    base_price = Decimal("50000.0")
    # Feed 20 bars with steady upward momentum, heavy green volume,
    # and strong lower wicks (buyer absorption)
    for i in range(20):
        t = now + timedelta(minutes=i)
        p_open = base_price + Decimal(str(i * 30))
        p_close = p_open + Decimal("25.0")  # Bullish candle
        p_low = p_open - Decimal("20.0")  # Long lower wick (buying absorption)
        p_high = p_close + Decimal("5.0")  # Small upper wick
        vol = Decimal("200.0")  # High buying volume

        bars.append(
            _create_bar(
                symbol="BTCUSDT",
                timeframe="1m",
                timestamp=t,
                open_=p_open,
                high=p_high,
                low=p_low,
                close=p_close,
                volume=vol,
            )
        )

    snapshot = engine.calculate_alpha(bars)
    assert snapshot is not None
    assert isinstance(snapshot, QlibAlphaSnapshot)
    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.timeframe == "1m"
    assert snapshot.momentum_factor > Decimal("0.0")
    assert snapshot.volume_flow_factor > Decimal("0.0")
    assert snapshot.wick_rejection_factor > Decimal("0.0")
    assert snapshot.composite_score >= Decimal("0.50")
    assert snapshot.is_high_conviction_long is True
    assert snapshot.is_high_conviction_short is False


def test_qlib_alpha_bearish_conviction() -> None:
    engine = QlibAlphaEngine(lookback_bars=20, conviction_threshold=Decimal("0.50"))
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    bars: list[Bar] = []

    base_price = Decimal("60000.0")
    # Feed 20 bars with steady downward momentum, red volume,
    # and long upper wicks (seller rejection)
    for i in range(20):
        t = now + timedelta(minutes=i)
        p_open = base_price - Decimal(str(i * 30))
        p_close = p_open - Decimal("25.0")  # Bearish candle
        p_high = p_open + Decimal("20.0")  # Long upper wick (sellers rejecting highs)
        p_low = p_close - Decimal("5.0")  # Small lower wick
        vol = Decimal("200.0")  # High volume

        bars.append(
            _create_bar(
                symbol="BTCUSDT",
                timeframe="1m",
                timestamp=t,
                open_=p_open,
                high=p_high,
                low=p_low,
                close=p_close,
                volume=vol,
            )
        )

    snapshot = engine.calculate_alpha(bars)
    assert snapshot is not None
    assert snapshot.momentum_factor < Decimal("0.0")
    assert snapshot.volume_flow_factor < Decimal("0.0")
    assert snapshot.wick_rejection_factor < Decimal("0.0")
    assert snapshot.composite_score <= Decimal("-0.50")
    assert snapshot.is_high_conviction_long is False
    assert snapshot.is_high_conviction_short is True


def test_qlib_alpha_neutral_sideways_chop() -> None:
    engine = QlibAlphaEngine(lookback_bars=20, conviction_threshold=Decimal("0.50"))
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    bars: list[Bar] = []

    # Alternating tiny candles with balanced volume and symmetric wicks
    for i in range(20):
        t = now + timedelta(minutes=i)
        if i % 2 == 0:
            p_open = Decimal("50000.0")
            p_close = Decimal("50005.0")
        else:
            p_open = Decimal("50005.0")
            p_close = Decimal("50000.0")

        bars.append(
            _create_bar(
                symbol="BTCUSDT",
                timeframe="1m",
                timestamp=t,
                open_=p_open,
                high=Decimal("50010.0"),
                low=Decimal("49995.0"),
                close=p_close,
                volume=Decimal("50.0"),
            )
        )

    snapshot = engine.calculate_alpha(bars)
    assert snapshot is not None
    assert abs(snapshot.composite_score) < Decimal("0.50")
    assert snapshot.is_high_conviction_long is False
    assert snapshot.is_high_conviction_short is False
