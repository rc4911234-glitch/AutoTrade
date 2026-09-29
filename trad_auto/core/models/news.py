"""Domain contracts for news articles, macro calendar releases, and sentiment scores."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import NewsImpactLevel, NewsSentimentType, NewsSourceType
from trad_auto.core.exceptions import DomainValidationError


@dataclass(frozen=True)
class NewsArticle:
    """Raw incoming news headline or wire report."""

    title: str
    source: str
    published_at: datetime
    source_type: NewsSourceType = NewsSourceType.CUSTOM
    url: str = ""
    symbols: list[str] = field(default_factory=list)
    raw_text: str = ""
    article_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise DomainValidationError("NewsArticle title must be non-empty")
        if not self.source.strip():
            raise DomainValidationError("NewsArticle source must be non-empty")
        if self.published_at.tzinfo is None or self.published_at.tzinfo != UTC:
            raise DomainValidationError("NewsArticle published_at must be timezone-aware (UTC)")


@dataclass(frozen=True)
class MacroEconomicEvent:
    """Scheduled high-impact macroeconomic event with defined volatility blackout windows."""

    title: str
    scheduled_at: datetime
    impact: NewsImpactLevel = NewsImpactLevel.HIGH
    cool_off_pre_minutes: int = 15
    cool_off_post_minutes: int = 15
    affected_symbols: list[str] = field(default_factory=lambda: ["*"])
    event_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise DomainValidationError("MacroEconomicEvent title must be non-empty")
        if self.scheduled_at.tzinfo is None or self.scheduled_at.tzinfo != UTC:
            raise DomainValidationError(
                "MacroEconomicEvent scheduled_at must be timezone-aware (UTC)"
            )
        if self.cool_off_pre_minutes < 0 or self.cool_off_post_minutes < 0:
            raise DomainValidationError("Blackout cool-off minutes must be non-negative")

    def is_in_blackout_window(
        self,
        now: datetime,
        pre_override: int | None = None,
        post_override: int | None = None,
    ) -> bool:
        """Determines if the given timestamp falls inside the event's volatility blackout window."""
        current_time = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        pre = self.cool_off_pre_minutes if pre_override is None else pre_override
        post = self.cool_off_post_minutes if post_override is None else post_override

        start_time = self.scheduled_at - timedelta(minutes=pre)
        end_time = self.scheduled_at + timedelta(minutes=post)
        return start_time <= current_time <= end_time

    def blackout_start_time(self, pre_override: int | None = None) -> datetime:
        """Calculates exact timestamp when entry blackout begins."""
        pre = self.cool_off_pre_minutes if pre_override is None else pre_override
        return self.scheduled_at - timedelta(minutes=pre)

    def blackout_end_time(self, post_override: int | None = None) -> datetime:
        """Calculates exact timestamp when entry blackout expires."""
        post = self.cool_off_post_minutes if post_override is None else post_override
        return self.scheduled_at + timedelta(minutes=post)

    def affects_symbol(self, symbol: str) -> bool:
        """Returns True if the event impacts the target symbol or entire market ('*')."""
        if "*" in self.affected_symbols:
            return True
        return symbol.upper() in [s.upper() for s in self.affected_symbols]


@dataclass(frozen=True)
class NewsSentimentResult:
    """Calculated sentiment classification and volatility impact assessment."""

    article_id: UUID
    headline: str
    sentiment: NewsSentimentType
    sentiment_score: Decimal  # Normalized -1.00 (Extreme Bearish) to +1.00 (Extreme Bullish)
    impact: NewsImpactLevel
    matched_keywords: list[str]
    confidence: Decimal
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if self.sentiment_score < Decimal("-1.00") or self.sentiment_score > Decimal("1.00"):
            raise DomainValidationError("sentiment_score must be between -1.00 and +1.00")
        if self.confidence < ZERO_DECIMAL or self.confidence > Decimal("1.00"):
            raise DomainValidationError("confidence must be between 0.00 and 1.00")
