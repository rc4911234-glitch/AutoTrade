"""Unit tests for LiveNewsPoller real-time crypto news ingestion and resilience."""

import json
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from trad_auto.core.enums import NewsSourceType
from trad_auto.core.time import SimulatedClock
from trad_auto.engine import TradingEngine
from trad_auto.health import ComponentStatus, HealthMonitor
from trad_auto.news.poller import LiveNewsPoller
from trad_auto.news.shield import NewsVolatilityShield

SAMPLE_CRYPTOPANIC_JSON = json.dumps(
    {
        "count": 2,
        "results": [
            {
                "id": 101,
                "title": "SEC approves spot Bitcoin and Ethereum ETF index fund",
                "url": "https://cryptopanic.com/news/101/sec-approves-etf",
                "domain": "bloomberg.com",
                "created_at": "2026-09-23T12:00:00Z",
                "currencies": [
                    {"code": "BTC", "title": "Bitcoin"},
                    {"code": "ETH", "title": "Ethereum"},
                ],
            },
            {
                "id": 102,
                "title": "Solana ecosystem transaction volume accelerates",
                "url": "https://cryptopanic.com/news/102/solana-volume",
                "domain": "coindesk.com",
                "created_at": "2026-09-23T12:05:00Z",
                "currencies": [{"code": "SOL", "title": "Solana"}],
            },
        ],
    }
).encode("utf-8")

SAMPLE_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>Crypto Wire News</title>
        <link>https://example.com</link>
        <description>Live Crypto News Feed</description>
        <item>
            <title>Bitcoin crosses all-time high amidst institutional accumulation</title>
            <link>https://example.com/btc-ath-rally</link>
            <pubDate>Wed, 23 Sep 2026 12:10:00 GMT</pubDate>
            <description>Major rally underway.</description>
        </item>
        <item>
            <title>Market analysis: Weekend macroeconomic expectations</title>
            <link>https://example.com/macro-analysis</link>
            <pubDate>Wed, 23 Sep 2026 12:15:00 GMT</pubDate>
            <description>General market overview.</description>
        </item>
    </channel>
</rss>
"""

SAMPLE_ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Cointelegraph Atom</title>
    <entry>
        <title>Ethereum layer-2 network TVL surges</title>
        <link href="https://example.com/eth-tvl-surge" />
        <updated>2026-09-23T12:20:00Z</updated>
    </entry>
</feed>
"""


def test_symbol_extraction() -> None:
    """Verifies regex identification and normalization of major crypto tickers."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield)

    # Specific symbols
    assert "BTCUSDT" in poller.extract_symbols("Breaking: Bitcoin supply shock incoming")
    assert "ETHUSDT" in poller.extract_symbols("Ethereum developers finalize core upgrade")
    assert "SOLUSDT" in poller.extract_symbols("Solana DeFi protocol raises $50M")
    assert "BNBUSDT" in poller.extract_symbols("Binance Coin breaks key resistance")
    assert "XRPUSDT" in poller.extract_symbols("Ripple XRP settlement finalized")
    assert "DOGEUSDT" in poller.extract_symbols("Dogecoin payment integration announced")

    # Multi-currency headline
    multi = poller.extract_symbols("Bitcoin and Ethereum rally together")
    assert "BTCUSDT" in multi and "ETHUSDT" in multi

    # No specific symbols -> Wildcard
    assert poller.extract_symbols("Global macroeconomic inflation figures released") == ["*"]


def test_cryptopanic_fetch_and_parse() -> None:
    """Tests CryptoPanic REST API fetching and JSON transformation."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield, api_token="test_token_123")

    mock_resp = MagicMock()
    mock_resp.read.return_value = SAMPLE_CRYPTOPANIC_JSON
    mock_resp.__enter__.return_value = mock_resp

    with patch("trad_auto.news.poller.urlopen", return_value=mock_resp):
        articles = poller.fetch_cryptopanic()

    assert len(articles) == 2
    art1, art2 = articles[0], articles[1]

    assert art1.title == "SEC approves spot Bitcoin and Ethereum ETF index fund"
    assert art1.source == "bloomberg.com"
    assert art1.source_type == NewsSourceType.CRYPTO_PANIC
    assert art1.published_at.tzinfo == UTC
    assert "BTCUSDT" in art1.symbols and "ETHUSDT" in art1.symbols

    assert art2.title == "Solana ecosystem transaction volume accelerates"
    assert "SOLUSDT" in art2.symbols


def test_cryptopanic_empty_token_returns_empty() -> None:
    """Verifies empty API token skips external call immediately."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield, api_token="")

    with patch("trad_auto.news.poller.urlopen") as mock_url:
        articles = poller.fetch_cryptopanic()
        assert articles == []
        mock_url.assert_not_called()


def test_rss_and_atom_xml_parsing() -> None:
    """Tests parsing both RSS 2.0 and Atom XML feeds with pubDate conversion."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield)

    # RSS 2.0
    rss_articles = poller.parse_rss_xml(SAMPLE_RSS_XML, fallback_source="CoinDesk")
    assert len(rss_articles) == 2
    assert (
        rss_articles[0].title == "Bitcoin crosses all-time high amidst institutional accumulation"
    )
    assert rss_articles[0].source == "CoinDesk"
    assert rss_articles[0].source_type == NewsSourceType.RSS_FEED
    assert rss_articles[0].url == "https://example.com/btc-ath-rally"
    assert rss_articles[0].published_at.tzinfo == UTC
    assert rss_articles[0].symbols == ["BTCUSDT"]

    # Wildcard symbol on generic headline
    assert rss_articles[1].symbols == ["*"]

    # Atom
    atom_articles = poller.parse_rss_xml(SAMPLE_ATOM_XML, fallback_source="Cointelegraph")
    assert len(atom_articles) == 1
    assert atom_articles[0].title == "Ethereum layer-2 network TVL surges"
    assert atom_articles[0].url == "https://example.com/eth-tvl-surge"
    assert "ETHUSDT" in atom_articles[0].symbols


def test_deduplication_and_ingestion_into_shield() -> None:
    """Verifies duplicate articles across multiple polling cycles are only ingested once."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield)

    mock_resp = MagicMock()
    mock_resp.read.return_value = SAMPLE_RSS_XML
    mock_resp.__enter__.return_value = mock_resp

    with patch("trad_auto.news.poller.urlopen", return_value=mock_resp):
        # First poll -> 2 new articles ingested
        discovered1 = poller.poll_now()
        assert len(discovered1) == 2
        assert poller.total_articles_ingested == 2

        # Second poll -> identical feed -> 0 newly ingested into shield
        discovered2 = poller.poll_now()
        assert len(discovered2) == 0
        assert poller.total_articles_ingested == 2  # No increase due to deduplication


def test_network_errors_and_timeout_resilience() -> None:
    """Tests that HTTP, network, and timeout errors do not crash the engine."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield, api_token="secret_key")

    # 1. HTTP 429 Rate Limit
    with patch(
        "trad_auto.news.poller.urlopen",
        side_effect=HTTPError(
            "https://cryptopanic.com", 429, "Too Many Requests", Message(), BytesIO(b"")
        ),
    ):
        result_429 = poller.poll_now()
        assert result_429 == []
        assert poller.consecutive_errors == 0  # CryptoPanic handled gracefully internally

    # 2. Complete network disconnection (URLError)
    with patch(
        "trad_auto.news.poller.urlopen",
        side_effect=URLError("DNS resolution failed"),
    ):
        result_url_err = poller.poll_now()
        assert result_url_err == []

    # 3. TimeoutError
    with patch(
        "trad_auto.news.poller.urlopen",
        side_effect=TimeoutError("Connection timed out"),
    ):
        result_timeout = poller.poll_now()
        assert result_timeout == []


def test_background_worker_thread_lifecycle() -> None:
    """Verifies starting, running, and graceful shutdown of polling daemon thread."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield, poll_interval_seconds=60)

    assert not poller.is_running

    # Start thread
    with patch.object(poller, "poll_now", return_value=[]):
        poller.start()
        assert poller.is_running

        # Calling start again while running is safe idempotent
        poller.start()
        assert poller.is_running

        # Stop thread
        poller.stop(timeout=1.0)
        assert not poller.is_running


def test_health_monitor_poller_diagnostics() -> None:
    """Verifies HealthMonitor tracks poller vitals and flags elevated consecutive errors."""
    t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    shield = NewsVolatilityShield(clock=clock)
    poller = LiveNewsPoller(shield=shield)

    health_mon = HealthMonitor(news_shield=shield, news_poller=poller, clock=clock)

    # 1. Healthy state
    poller.total_articles_ingested = 15
    poller.last_poll_time = t0
    report = health_mon.get_health_report()

    assert report.is_healthy is True
    details = report.components["news_shield"].details
    assert details["poller_active"] is False
    assert details["total_articles_ingested"] == 15
    assert details["last_poll_time"] == t0.isoformat()

    # 2. Elevated errors -> Component degraded
    poller.consecutive_errors = 6
    report_deg = health_mon.get_health_report()
    assert report_deg.status == ComponentStatus.DEGRADED
    assert report_deg.components["news_shield"].status == ComponentStatus.DEGRADED
    shield_msg = report_deg.components["news_shield"].message
    assert "News poller failing (6 consecutive errors)" in shield_msg


def test_trading_engine_poller_wiring() -> None:
    """Verifies TradingEngine instantiates, starts, and stops LiveNewsPoller."""
    engine = TradingEngine()
    assert engine.news_poller is not None
    assert engine.news_poller.shield is engine.news_shield

    # Start engine starts news_poller
    with (
        patch.object(engine.market_data_adapter, "connect"),
        patch.object(engine.market_data_adapter, "subscribe"),
        patch.object(engine.news_poller, "start") as mock_start,
        patch.object(engine.news_poller, "stop") as mock_stop,
    ):
        engine.start()
        mock_start.assert_called_once()

        engine.stop()
        mock_stop.assert_called_once()


def test_poller_edge_branches_and_cache_eviction() -> None:
    """Tests LRU cache eviction at >2000 items, malformed XML, empty titles, and invalid dates."""
    shield = NewsVolatilityShield()
    poller = LiveNewsPoller(shield=shield)

    # 1. Empty seen ID returns False
    assert poller._record_seen("") is False
    assert poller._record_seen("   ") is False

    # 2. Cache eviction triggers at > 2000
    for i in range(2005):
        poller._record_seen(f"item_id_{i}")
    assert len(poller._seen_ids) <= 2000
    assert "item_id_0" not in poller._seen_ids  # Oldest evicted
    assert "item_id_2004" in poller._seen_ids

    # 3. Domain extraction fallback
    assert poller._domain_from_url("not_a_valid_url") == "CryptoRSS"

    # 4. Malformed XML returns empty list
    assert poller.parse_rss_xml(b"<malformed << xml>") == []

    # 5. Item without title is skipped
    xml_no_title = (
        b"""<rss version="2.0"><channel><item><link>https://a.com</link></item></channel></rss>"""
    )
    assert poller.parse_rss_xml(xml_no_title) == []

    # 6. Item with unparseable pubDate falls back safely
    xml_bad_date = (
        b"<rss version='2.0'><channel><item>"
        b"<title>Test News</title><pubDate>InvalidDate</pubDate>"
        b"</item></channel></rss>"
    )
    bad_date_articles = poller.parse_rss_xml(xml_bad_date)
    assert len(bad_date_articles) == 1
    assert bad_date_articles[0].published_at.tzinfo == UTC

    # 7. CryptoPanic items with empty title and invalid date
    poller_cp = LiveNewsPoller(shield=shield, api_token="dummy_token")
    mock_cp_data = json.dumps(
        {
            "results": [
                {"title": "   "},  # Empty title -> skipped
                {
                    "title": "Valid News",
                    "created_at": "invalid_date_format",
                    "currencies": [],
                },  # Regex symbol fallback
            ]
        }
    ).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_cp_data
    mock_resp.__enter__.return_value = mock_resp

    with patch("trad_auto.news.poller.urlopen", return_value=mock_resp):
        cp_res = poller_cp.fetch_cryptopanic()
        assert len(cp_res) == 1
        assert cp_res[0].title == "Valid News"

    # 8. Stop when not running returns early
    poller.stop()  # Doesn't raise when not running

    # 9. Poll now handles unhandled exception gracefully
    with patch.object(poller, "fetch_rss_feeds", side_effect=RuntimeError("Unexpected crash")):
        res_crash = poller.poll_now()
        assert res_crash == []
        assert poller.consecutive_errors == 1
        assert poller.total_errors == 1
