"""News and Macro Event Volatility Shield subsystem."""

from trad_auto.core.models.news import MacroEconomicEvent, NewsArticle, NewsSentimentResult
from trad_auto.news.calendar import MacroCalendarManager
from trad_auto.news.poller import LiveNewsPoller
from trad_auto.news.sentiment import NewsSentimentAnalyzer
from trad_auto.news.shield import NewsVolatilityShield

__all__ = [
    "LiveNewsPoller",
    "MacroCalendarManager",
    "MacroEconomicEvent",
    "NewsArticle",
    "NewsSentimentAnalyzer",
    "NewsSentimentResult",
    "NewsVolatilityShield",
]
