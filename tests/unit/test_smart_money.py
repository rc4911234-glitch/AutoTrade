"""Unit tests for BinanceSmartMoneyAnalyzer."""

from decimal import Decimal
from unittest.mock import patch

from trad_auto.ai.smart_money import BinanceSmartMoneyAnalyzer
from trad_auto.core.enums import OrderSide


def test_smart_money_metrics_parsing() -> None:
    analyzer = BinanceSmartMoneyAnalyzer()

    mock_pos = [
        {
            "symbol": "BTCUSDT",
            "longShortRatio": "1.80",
            "longAccount": "0.64",
            "shortAccount": "0.36",
        }
    ]
    mock_acc = [{"symbol": "BTCUSDT", "longShortRatio": "1.75"}]
    mock_taker = [{"buySellRatio": "1.25"}]

    with patch.object(analyzer, "_get_json", side_effect=[mock_pos, mock_acc, mock_taker]):
        metrics = analyzer.fetch_smart_money_metrics("BTCUSDT")
        assert metrics is not None
        assert metrics.symbol == "BTCUSDT"
        assert metrics.top_trader_position_ratio == Decimal("1.80")
        assert metrics.taker_buy_sell_ratio == Decimal("1.25")
        assert metrics.raw_whale_long_pct == Decimal("0.64")
        assert metrics.sentiment_score > Decimal("0")


def test_smart_money_blocks_opposing_extreme_sentiment() -> None:
    analyzer = BinanceSmartMoneyAnalyzer()

    # Extreme short: L/S = 0.40 (2.5x short)
    mock_pos_bear = [
        {
            "symbol": "BTCUSDT",
            "longShortRatio": "0.40",
            "longAccount": "0.28",
            "shortAccount": "0.72",
        }
    ]
    mock_acc_bear = [{"symbol": "BTCUSDT", "longShortRatio": "0.45"}]
    mock_taker_bear = [{"buySellRatio": "0.60"}]

    with patch.object(
        analyzer, "_get_json", side_effect=[mock_pos_bear, mock_acc_bear, mock_taker_bear]
    ):
        approved, reason = analyzer.validate_proposal_alignment("BTCUSDT", OrderSide.BUY)
        assert approved is False
        assert "Blocked by Smart Money Shield" in reason
        assert "net SHORT" in reason

    # Extreme long: L/S = 2.50 (2.5x long)
    mock_pos_bull = [
        {
            "symbol": "BTCUSDT",
            "longShortRatio": "2.50",
            "longAccount": "0.71",
            "shortAccount": "0.29",
        }
    ]
    mock_acc_bull = [{"symbol": "BTCUSDT", "longShortRatio": "2.30"}]
    mock_taker_bull = [{"buySellRatio": "1.40"}]

    with patch.object(
        analyzer, "_get_json", side_effect=[mock_pos_bull, mock_acc_bull, mock_taker_bull]
    ):
        approved, reason = analyzer.validate_proposal_alignment("BTCUSDT", OrderSide.SELL)
        assert approved is False
        assert "Blocked by Smart Money Shield" in reason
        assert "net LONG" in reason


def test_smart_money_approves_aligned_sentiment() -> None:
    analyzer = BinanceSmartMoneyAnalyzer()

    mock_pos = [
        {
            "symbol": "BTCUSDT",
            "longShortRatio": "1.60",
            "longAccount": "0.61",
            "shortAccount": "0.39",
        }
    ]
    mock_acc = [{"symbol": "BTCUSDT", "longShortRatio": "1.50"}]
    mock_taker = [{"buySellRatio": "1.10"}]

    with patch.object(analyzer, "_get_json", side_effect=[mock_pos, mock_acc, mock_taker]):
        approved, reason = analyzer.validate_proposal_alignment("BTCUSDT", OrderSide.BUY)
        assert approved is True
        assert "Smart Money Aligned" in reason


def test_smart_money_fails_open_on_network_error() -> None:
    analyzer = BinanceSmartMoneyAnalyzer()

    with patch.object(analyzer, "_get_json", return_value=None):
        approved, reason = analyzer.validate_proposal_alignment("BTCUSDT", OrderSide.BUY)
        assert approved is True
        assert "temporarily unavailable" in reason
