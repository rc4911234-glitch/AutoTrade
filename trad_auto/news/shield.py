"""News volatility shield coordinating macro blackouts and sentiment trade filters."""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import NewsImpactLevel, OrderSide
from trad_auto.core.events import (
    NewsArticleReceivedEvent,
    NewsShieldBlackoutEngagedEvent,
)
from trad_auto.core.models.news import MacroEconomicEvent, NewsArticle, NewsSentimentResult
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.time import Clock, SystemClock
from trad_auto.news.calendar import MacroCalendarManager
from trad_auto.news.sentiment import NewsSentimentAnalyzer

logger = logging.getLogger(__name__)


class NewsVolatilityShield:
    """Institutional volatility firewall protecting capital against news spikes.

    Core Capabilities:
    1. Macro Calendar Blackouts: Blocks trade entries around scheduled releases (CPI, FOMC, NFP).
    2. Breaking News Flash Blackouts: Critical incidents (hacks, insolvency, depegging) trigger
       immediate temporary blackout windows (default 30 mins).
    3. Sentiment Alignment Guard: Rejects technical proposals conflicting with severe opposing news.
    4. Event Publishing: Dispatches domain events when blackout states engage/disengage.
    """

    def __init__(
        self,
        calendar_mgr: MacroCalendarManager | None = None,
        sentiment_analyzer: NewsSentimentAnalyzer | None = None,
        event_bus: EventBus | None = None,
        clock: Clock | None = None,
        breaking_news_blackout_minutes: int = 10,
        enforce_sentiment_alignment: bool = True,
        sentiment_rejection_threshold: Decimal = Decimal("0.60"),
    ) -> None:
        self.calendar_mgr = calendar_mgr or MacroCalendarManager()
        self.sentiment_analyzer = sentiment_analyzer or NewsSentimentAnalyzer()
        self.event_bus = event_bus
        self.clock: Clock = clock or SystemClock()
        self.breaking_news_blackout_minutes = breaking_news_blackout_minutes
        self.enforce_sentiment_alignment = enforce_sentiment_alignment
        self.sentiment_rejection_threshold = sentiment_rejection_threshold

        # Tracks breaking news flash blackouts: symbol -> blackout_end_time
        self._breaking_blackouts: dict[str, tuple[str, datetime]] = {}
        # Recent sentiment results history: list of (timestamp, symbol, NewsSentimentResult)
        self._recent_sentiment: list[tuple[datetime, str, NewsSentimentResult]] = []

    def register_macro_event(self, event: MacroEconomicEvent) -> None:
        """Enrolls a scheduled high-impact economic release."""
        self.calendar_mgr.register_event(event)

    def ingest_article(self, article: NewsArticle) -> NewsSentimentResult:
        """Processes an incoming news report, calculates sentiment, and applies safety actions."""
        now = self.clock.now()
        result = self.sentiment_analyzer.analyze_article(article, now=now)

        symbols = [s.upper() for s in article.symbols] if article.symbols else []
        if not symbols:
            title_lower = article.title.lower()
            if any(term in title_lower for term in ("btc", "bitcoin")):
                symbols = ["BTCUSDT"]
            elif any(term in title_lower for term in ("eth", "ethereum")):
                symbols = ["ETHUSDT"]
            elif any(term in title_lower for term in ("sol", "solana")):
                symbols = ["SOLUSDT"]
            elif any(term in title_lower for term in ("binance", "tether", "usdt", "sec", "fed", "treasury")):
                symbols = ["*"]
            else:
                symbols = []

        primary_symbol = symbols[0] if symbols else ""
        self._recent_sentiment.append((now, primary_symbol or "*", result))

        # Publish domain event
        if self.event_bus:
            self.event_bus.publish(NewsArticleReceivedEvent(article=article))

        # Critical severity incidents trigger automatic breaking blackout on affected symbols only
        if result.impact == NewsImpactLevel.CRITICAL and symbols:
            end_time = now + timedelta(minutes=self.breaking_news_blackout_minutes)
            for sym in symbols:
                self._breaking_blackouts[sym] = (article.title, end_time)
            logger.warning(
                "🚨 CRITICAL BREAKING NEWS: '%s'. Engaging %dm blackout for %s until %s",
                article.title,
                self.breaking_news_blackout_minutes,
                symbols,
                end_time.isoformat(),
            )
            if self.event_bus:
                self.event_bus.publish(
                    NewsShieldBlackoutEngagedEvent(
                        title=article.title,
                        scheduled_at=now,
                        blackout_end=end_time,
                        affected_symbols=symbols,
                        reason=f"Critical breaking news: {article.title}",
                    )
                )

        return result

    def is_blackout_active(self, symbol: str, now: datetime | None = None) -> bool:
        """Returns True if any macro calendar or breaking news blackout is active for symbol."""
        current_time = now or self.clock.now()

        # 1. Check macro calendar
        if self.calendar_mgr.is_in_blackout(current_time, symbol=symbol):
            return True

        # 2. Check breaking news blackout
        for target in (symbol.upper(), "*"):
            if target in self._breaking_blackouts:
                _, end_time = self._breaking_blackouts[target]
                if current_time <= end_time:
                    return True

        return False

    def get_recent_sentiment_score(
        self,
        symbol: str | None = None,
        max_age_minutes: int = 60,
        now: datetime | None = None,
    ) -> Decimal:
        """Returns the average sentiment score from recent news within the time window."""
        current_time = now or self.clock.now()
        cutoff = current_time - timedelta(minutes=max_age_minutes)

        relevant_scores: list[Decimal] = []
        for ts, sym, res in self._recent_sentiment:
            if ts >= cutoff:
                if symbol is None or sym in (symbol.upper(), "*"):
                    relevant_scores.append(res.sentiment_score)

        if not relevant_scores:
            return ZERO_DECIMAL

        return round(sum(relevant_scores, ZERO_DECIMAL) / Decimal(len(relevant_scores)), 2)

    def check_trade_proposal(
        self,
        proposal: TradeProposal,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Validates a strategy TradeProposal against active news blackouts and sentiment bias.

        Returns:
            (is_allowed: bool, rejection_reason: str)
        """
        current_time = now or proposal.timestamp

        # 1. Macro Calendar Blackout Check
        active_macro = self.calendar_mgr.get_active_blackout_events(
            current_time, symbol=proposal.symbol
        )
        if active_macro:
            event = active_macro[0]
            reason = (
                f"NewsVolatilityShield: Active macro blackout for '{event.title}' "
                f"until {event.blackout_end_time().strftime('%H:%M:%S UTC')}"
            )
            return False, reason

        # 2. Breaking News Blackout Check
        for target in (proposal.symbol.upper(), "*"):
            if target in self._breaking_blackouts:
                title, end_time = self._breaking_blackouts[target]
                if current_time <= end_time:
                    reason = (
                        f"NewsVolatilityShield: Active breaking news blackout for '{title}' "
                        f"until {end_time.strftime('%H:%M:%S UTC')}"
                    )
                    return False, reason

        # 3. Sentiment Alignment Check
        if self.enforce_sentiment_alignment:
            sentiment = self.get_recent_sentiment_score(symbol=proposal.symbol, now=current_time)
            thresh = self.sentiment_rejection_threshold
            if proposal.direction == OrderSide.BUY and sentiment <= -thresh:
                return (
                    False,
                    f"NewsVolatilityShield: BUY proposal conflicts with severe "
                    f"negative news sentiment ({sentiment} <= -{thresh})",
                )
            # Rejection: Technical SELL while news is severely Bullish
            if proposal.direction == OrderSide.SELL and sentiment >= thresh:
                return (
                    False,
                    f"NewsVolatilityShield: SELL proposal conflicts with severe "
                    f"positive news sentiment ({sentiment} >= +{thresh})",
                )

        return True, ""
