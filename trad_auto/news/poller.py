"""Live real-time news poller ingesting CryptoPanic REST API and public crypto RSS feeds."""

import email.utils
import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trad_auto.core.enums import NewsSourceType
from trad_auto.core.models.news import NewsArticle
from trad_auto.news.shield import NewsVolatilityShield

logger = logging.getLogger(__name__)

# Known crypto ticker pattern matcher
SYMBOL_REGEX_MAP: dict[str, re.Pattern[str]] = {
    "BTCUSDT": re.compile(r"\b(btc|bitcoin)\b", re.IGNORECASE),
    "ETHUSDT": re.compile(r"\b(eth|ethereum|ether)\b", re.IGNORECASE),
    "SOLUSDT": re.compile(r"\b(sol|solana)\b", re.IGNORECASE),
    "BNBUSDT": re.compile(r"\b(bnb|binance\s+coin)\b", re.IGNORECASE),
    "XRPUSDT": re.compile(r"\b(xrp|ripple)\b", re.IGNORECASE),
    "DOGEUSDT": re.compile(r"\b(doge|dogecoin)\b", re.IGNORECASE),
}

DEFAULT_RSS_FEEDS: list[str] = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "https://bitcoinmagazine.com/feed",
    "https://news.google.com/rss/search?q=crypto+bitcoin+binance+when:1d&hl=en-US&gl=US&ceid=US:en",
]


class LiveNewsPoller:
    """Automated live news ingestion poller protecting capital in real time.

    Features:
    - Dual ingestion: CryptoPanic REST API (authenticated) or Public Crypto RSS (zero-key fallback).
    - Sub-millisecond URL de-duplication to prevent re-processing identical wire stories.
    - Automatic crypto ticker symbol extraction (BTC, ETH, SOL, BNB, XRP, DOGE).
    - Clean daemon thread lifecycle with immediate shutdown via threading.Event.
    - Direct integration with NewsVolatilityShield for sentiment evaluation and flash blackouts.
    """

    def __init__(
        self,
        shield: NewsVolatilityShield,
        api_token: str = "",
        rss_urls: list[str] | None = None,
        poll_interval_seconds: int = 60,
        timeout_seconds: float = 10.0,
        user_agent: str = "TradAuto/1.0 (+https://tradauto.local; NewsVolatilityShield)",
    ) -> None:
        self.shield = shield
        self.api_token = api_token.strip()
        self.rss_urls = list(rss_urls) if rss_urls is not None else list(DEFAULT_RSS_FEEDS)
        self.poll_interval_seconds = max(5, poll_interval_seconds)
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.user_agent = user_agent

        # De-duplication tracking
        self._seen_ids: set[str] = set()
        self._seen_ids_order: list[str] = []
        self._lock = threading.Lock()

        # Threading state
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Telemetry
        self.last_poll_time: datetime | None = None
        self.total_articles_ingested: int = 0
        self.consecutive_errors: int = 0
        self.total_errors: int = 0

    @property
    def is_running(self) -> bool:
        """Returns True if the background polling loop is active and running."""
        alive = self._thread is not None and self._thread.is_alive()
        return alive and not self._stop_event.is_set()

    def start(self) -> None:
        """Starts background polling daemon thread."""
        if self.is_running:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="tradauto-news-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "LiveNewsPoller started (interval=%ds, mode=%s)",
            self.poll_interval_seconds,
            "CryptoPanic+RSS" if self.api_token else "Public RSS Feeds",
        )

    def stop(self, timeout: float = 2.0) -> None:
        """Signals background loop to terminate and joins thread cleanly."""
        if not self.is_running:
            return

        logger.info("LiveNewsPoller stopping...")
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("LiveNewsPoller stopped cleanly")

    def _run_loop(self) -> None:
        """Background thread worker loop executing periodic polls."""
        while not self._stop_event.is_set():
            try:
                self.poll_now()
            except Exception as exc:
                logger.error("Unexpected error in news polling cycle: %s", exc)

            # Wait for poll_interval or until stop_event is set
            self._stop_event.wait(timeout=float(self.poll_interval_seconds))

    def poll_now(self) -> list[NewsArticle]:
        """Executes a single synchronous poll across configured news sources.

        Fetches new headlines, de-duplicates, and feeds them into NewsVolatilityShield.
        Returns list of newly discovered and ingested NewsArticle instances.
        """
        now = datetime.now(UTC)
        self.last_poll_time = now
        discovered_articles: list[NewsArticle] = []

        try:
            # 1. Fetch from CryptoPanic if API key is provided
            if self.api_token:
                cp_articles = self.fetch_cryptopanic()
                discovered_articles.extend(cp_articles)

            # 2. Fetch from RSS Feeds (always fallback or primary)
            if not self.api_token or not discovered_articles:
                rss_articles = self.fetch_rss_feeds()
                discovered_articles.extend(rss_articles)

            # Ingest discovered articles into NewsVolatilityShield
            newly_ingested: list[NewsArticle] = []
            for article in discovered_articles:
                if self._record_seen(article.url or article.title):
                    self.shield.ingest_article(article)
                    newly_ingested.append(article)

            self.total_articles_ingested += len(newly_ingested)
            self.consecutive_errors = 0
            if newly_ingested:
                logger.info(
                    "LiveNewsPoller ingested %d fresh articles (total=%d)",
                    len(newly_ingested),
                    self.total_articles_ingested,
                )
            return newly_ingested

        except Exception as exc:
            self.consecutive_errors += 1
            self.total_errors += 1
            logger.warning(
                "LiveNewsPoller encountered error during poll cycle (consecutive=%d): %s",
                self.consecutive_errors,
                exc,
            )
            return []

    def fetch_cryptopanic(self) -> list[NewsArticle]:
        """Queries CryptoPanic REST API for latest market news."""
        if not self.api_token:
            return []

        url = f"https://cryptopanic.com/api/v1/posts/?auth_token={self.api_token}&public=true"
        req = Request(url, headers={"User-Agent": self.user_agent})

        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("CryptoPanic API request failed: %s", exc)
            return []

        results = data.get("results", [])
        articles: list[NewsArticle] = []

        for item in results:
            title = str(item.get("title", "")).strip()
            if not title:
                continue

            link = str(item.get("url", "")).strip()
            domain = str(item.get("domain", "CryptoPanic")).strip() or "CryptoPanic"
            created_str = item.get("created_at")

            published_at = datetime.now(UTC)
            if isinstance(created_str, str) and created_str:
                try:
                    # ISO 8601 parsing e.g. 2026-09-23T12:00:00Z
                    dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    published_at = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
                except ValueError:
                    pass

            # Currencies extraction from CryptoPanic payload
            symbols: list[str] = []
            for cur in item.get("currencies", []):
                code = cur.get("code")
                if isinstance(code, str) and code:
                    symbols.append(f"{code.upper()}USDT")

            # Fallback to regex matcher if empty
            if not symbols:
                symbols = self.extract_symbols(title)

            articles.append(
                NewsArticle(
                    title=title,
                    source=domain,
                    published_at=published_at,
                    source_type=NewsSourceType.CRYPTO_PANIC,
                    url=link,
                    symbols=symbols,
                )
            )

        return articles

    def fetch_rss_feeds(self) -> list[NewsArticle]:
        """Fetches and parses public crypto RSS XML feeds."""
        articles: list[NewsArticle] = []

        for feed_url in self.rss_urls:
            req = Request(feed_url, headers={"User-Agent": self.user_agent})
            try:
                with urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw_xml = resp.read()
                feed_articles = self.parse_rss_xml(
                    raw_xml, fallback_source=self._domain_from_url(feed_url)
                )
                articles.extend(feed_articles)
            except (HTTPError, URLError, TimeoutError, ET.ParseError) as exc:
                logger.debug("RSS feed fetch failed for '%s': %s", feed_url, exc)
                continue

        return articles

    def parse_rss_xml(
        self, raw_xml: bytes, fallback_source: str = "CryptoRSS"
    ) -> list[NewsArticle]:
        """Parses RSS/Atom XML payload into normalized NewsArticle instances."""
        articles: list[NewsArticle] = []
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return []

        # RSS 2.0: channel -> item
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")

        # Fallback to Atom: <feed> -> <entry>
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items:
            title = self._get_node_text(item, "title")
            if not title:
                continue

            link = self._get_node_text(item, "link")
            # If Atom entry, link might be in attribute 'href'
            if not link:
                link_elem = item.find("{http://www.w3.org/2005/Atom}link")
                if link_elem is not None:
                    link = link_elem.get("href", "")

            # Published date parsing
            pub_date_str = (
                self._get_node_text(item, "pubDate")
                or self._get_node_text(item, "published")
                or self._get_node_text(item, "updated")
            )
            published_at = datetime.now(UTC)
            if pub_date_str:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date_str)
                    published_at = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
                except Exception:
                    try:
                        dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                        published_at = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
                    except Exception:
                        pass

            symbols = self.extract_symbols(title)
            articles.append(
                NewsArticle(
                    title=title,
                    source=fallback_source,
                    published_at=published_at,
                    source_type=NewsSourceType.RSS_FEED,
                    url=link,
                    symbols=symbols,
                )
            )

        return articles

    def extract_symbols(self, text: str) -> list[str]:
        """Extracts crypto ticker symbols mentioned in the headline or text."""
        matched: list[str] = []
        for symbol, pattern in SYMBOL_REGEX_MAP.items():
            if pattern.search(text):
                matched.append(symbol)

        return matched if matched else ["*"]

    def _record_seen(self, item_id: str) -> bool:
        """Records an article identifier in the de-duplication cache.

        Returns True if fresh/unseen, False if already observed.
        """
        clean_id = item_id.strip()
        if not clean_id:
            return False

        with self._lock:
            if clean_id in self._seen_ids:
                return False

            self._seen_ids.add(clean_id)
            self._seen_ids_order.append(clean_id)

            # Evict oldest items if cache grows beyond 2000 elements
            if len(self._seen_ids) > 2000:
                to_remove = self._seen_ids_order[:500]
                self._seen_ids_order = self._seen_ids_order[500:]
                for old in to_remove:
                    self._seen_ids.discard(old)

            return True

    @staticmethod
    def _domain_from_url(url: str) -> str:
        """Extracts clean source domain from URL."""
        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if match:
            return match.group(1).capitalize()
        return "CryptoRSS"

    @staticmethod
    def _get_node_text(element: ET.Element, tag: str) -> str:
        """Extracts text content from child node handling namespace prefixes."""
        # Try direct tag
        val = element.findtext(tag)
        if val:
            return val.strip()

        # Try searching with wildcard namespace
        for child in element:
            if child.tag.endswith(f"}}{tag}") or child.tag == tag:
                if child.text:
                    return child.text.strip()

        return ""
