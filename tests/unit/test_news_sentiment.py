"""Unit tests for deterministic crypto news sentiment analysis and impact grading."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.enums import NewsImpactLevel, NewsSentimentType, NewsSourceType
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.news import NewsArticle, NewsSentimentResult
from trad_auto.news.sentiment import NewsSentimentAnalyzer


def test_article_validation_rules() -> None:
    """Verifies NewsArticle domain constraints on titles, sources, and timezones."""
    valid_time = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    # Valid article
    art = NewsArticle(
        title="SEC Approves Bitcoin Index ETF",
        source="Bloomberg",
        published_at=valid_time,
        symbols=["BTCUSDT"],
    )
    assert art.title == "SEC Approves Bitcoin Index ETF"
    assert art.symbols == ["BTCUSDT"]
    assert art.source_type == NewsSourceType.CUSTOM

    # Empty title
    with pytest.raises(DomainValidationError, match="title must be non-empty"):
        NewsArticle(title="   ", source="Reuters", published_at=valid_time)

    # Empty source
    with pytest.raises(DomainValidationError, match="source must be non-empty"):
        NewsArticle(title="Valid Title", source="", published_at=valid_time)

    # Naive timezone
    naive_time = datetime(2026, 9, 23, 12, 0)
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        NewsArticle(title="Valid Title", source="Reuters", published_at=naive_time)


def test_bullish_keywords_and_phrases() -> None:
    """Tests detection and scoring of single and multi-word bullish terminology."""
    analyzer = NewsSentimentAnalyzer()

    # Multi-word phrase 'etf approved' + 'inflows'
    headline = "Spot Bitcoin ETF approved by regulator with massive inflows"
    score, keywords, impact = analyzer.analyze_headline(headline)

    assert score > Decimal("0.50")
    assert "etf approved" in keywords or "approved" in keywords
    assert impact in (NewsImpactLevel.HIGH, NewsImpactLevel.CRITICAL)

    # All-time high
    score_ath, keywords_ath, _ = analyzer.analyze_headline("Bitcoin hits new all-time high")
    assert score_ath > Decimal("0.40")
    assert any("all-time high" in k or "ath" in k for k in keywords_ath)


def test_bearish_keywords_and_impact() -> None:
    """Tests detection of bearish news items and negative score generation."""
    analyzer = NewsSentimentAnalyzer()

    headline = "Regulator issues SEC lawsuit and subpoena against exchange"
    score, keywords, impact = analyzer.analyze_headline(headline)

    assert score < Decimal("-0.60")
    assert any(k in ("sec lawsuit", "lawsuit", "subpoena") for k in keywords)
    assert impact in (NewsImpactLevel.HIGH, NewsImpactLevel.CRITICAL)


def test_critical_security_and_insolvency_events() -> None:
    """Verifies that catastrophic events trigger CRITICAL impact regardless of wording."""
    analyzer = NewsSentimentAnalyzer()

    headlines = [
        "Major cross-chain bridge exploited and hacked for $200M",
        "Lending platform files for bankruptcy as assets become insolvent",
        "Algorithmic stablecoin experiences sudden depeg below $0.70",
    ]

    for hl in headlines:
        score, keywords, impact = analyzer.analyze_headline(hl)
        assert score <= Decimal("-0.60")
        assert impact == NewsImpactLevel.CRITICAL


def test_negation_awareness() -> None:
    """Tests that negating words prevent false panic triggers."""
    analyzer = NewsSentimentAnalyzer()

    # "not hacked" vs "hacked"
    score_clean, keywords_clean, impact_clean = analyzer.analyze_headline(
        "Exchange confirms platform was not hacked after routine maintenance"
    )
    assert any("negated" in k for k in keywords_clean)
    assert impact_clean != NewsImpactLevel.CRITICAL
    assert score_clean > Decimal("-0.50")

    # "denies bankruptcy"
    score_denies, keywords_denies, _ = analyzer.analyze_headline(
        "Company denies insolvency rumors in official statement"
    )
    assert any("negated" in k for k in keywords_denies)
    assert score_denies > Decimal("-0.50")


def test_neutral_and_sideways_sentiment() -> None:
    """Verifies unopinionated market commentary generates NEUTRAL classification."""
    analyzer = NewsSentimentAnalyzer()

    headline = "Bitcoin consolidates between 64000 and 65000 ahead of weekend"
    score, keywords, impact = analyzer.analyze_headline(headline)

    assert abs(score) < Decimal("0.20")
    assert impact == NewsImpactLevel.LOW


def test_analyze_article_full_contract() -> None:
    """Verifies NewsArticle to NewsSentimentResult transformation."""
    analyzer = NewsSentimentAnalyzer()
    now = datetime(2026, 9, 23, 14, 30, tzinfo=UTC)

    article = NewsArticle(
        title="MicroStrategy announces new massive treasury reserve buying rally",
        source="CoinDesk",
        published_at=now,
        symbols=["BTCUSDT"],
    )

    result = analyzer.analyze_article(article, now=now)

    assert isinstance(result, NewsSentimentResult)
    assert result.article_id == article.article_id
    assert result.headline == article.title
    assert result.sentiment == NewsSentimentType.BULLISH
    assert result.sentiment_score > Decimal("0.40")
    assert result.confidence > Decimal("0.30")
    assert result.evaluated_at == now
    assert len(result.matched_keywords) >= 1


def test_custom_lexicon_injection() -> None:
    """Tests that analyzer works with injected customized dictionary."""
    custom_bullish = {"superpump": Decimal("0.85")}
    custom_bearish = {"rekt": Decimal("0.95")}

    analyzer = NewsSentimentAnalyzer(
        bullish_lexicon=custom_bullish,
        bearish_lexicon=custom_bearish,
    )

    score_bull, kw_bull, _ = analyzer.analyze_headline("Market prepares for a superpump")
    assert score_bull == Decimal("0.85")
    assert kw_bull == ["superpump"]

    score_bear, kw_bear, impact_bear = analyzer.analyze_headline("Traders got rekt")
    assert score_bear == Decimal("-0.95")
    assert kw_bear == ["rekt"]
    assert impact_bear == NewsImpactLevel.CRITICAL


def test_neutral_article_and_zero_keywords() -> None:
    """Tests article with zero keywords falling back to low confidence and neutral sentiment."""
    analyzer = NewsSentimentAnalyzer()
    now = datetime(2026, 9, 23, 15, 0, tzinfo=UTC)

    article = NewsArticle(
        title="Weekend trading volume summary and weekly candle closing review",
        source="CoinDesk",
        published_at=now,
    )
    result = analyzer.analyze_article(article, now=now)
    assert result.sentiment == NewsSentimentType.NEUTRAL
    assert result.confidence == Decimal("0.10")
    assert result.matched_keywords == []


def test_negated_phrases_and_keywords() -> None:
    """Verifies two-word lookback and phrase negation behavior."""
    analyzer = NewsSentimentAnalyzer()

    # Negated phrase
    score, kw, _ = analyzer.analyze_headline("Lawyers confirm there is no sec lawsuit filed")
    assert any("negated" in k for k in kw)

    # Negated bullish single token
    score_not_app, kw_not_app, _ = analyzer.analyze_headline(
        "Regulator says proposal is not approved"
    )
    assert any("negated_approved" in k for k in kw_not_app)


def test_score_clamping_bounds() -> None:
    """Verifies that cumulative scores strictly clamp at +1.00 and -1.00."""
    analyzer = NewsSentimentAnalyzer()

    # Massive bullish confluence
    headline_bull = "All-time high rally breakout approval adoption etf approved rate cut"
    score_bull, _, _ = analyzer.analyze_headline(headline_bull)
    assert score_bull == Decimal("1.00")

    # Massive bearish confluence
    headline_bear = "Hack exploit scam fraud insolvent crash plunge bankruptcy ban lawsuit"
    score_bear, _, _ = analyzer.analyze_headline(headline_bear)
    assert score_bear == Decimal("-1.00")
