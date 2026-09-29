"""Deterministic NLP sentiment analyzer and impact classification for crypto news."""

import re
from datetime import UTC, datetime
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import NewsImpactLevel, NewsSentimentType
from trad_auto.core.models.news import NewsArticle, NewsSentimentResult

# Curated institutional lexicon with fractional decimal weights
BULLISH_KEYWORDS: dict[str, Decimal] = {
    "etf approved": Decimal("0.90"),
    "approval": Decimal("0.70"),
    "approved": Decimal("0.70"),
    "rate cut": Decimal("0.80"),
    "adoption": Decimal("0.50"),
    "partnership": Decimal("0.50"),
    "accumulate": Decimal("0.50"),
    "accumulation": Decimal("0.50"),
    "inflows": Decimal("0.60"),
    "all-time high": Decimal("0.65"),
    "ath": Decimal("0.60"),
    "bullish": Decimal("0.55"),
    "legal victory": Decimal("0.80"),
    "dismissed": Decimal("0.70"),
    "upgrade": Decimal("0.45"),
    "mainnet": Decimal("0.40"),
    "treasury reserve": Decimal("0.75"),
    "buying": Decimal("0.40"),
    "breakout": Decimal("0.40"),
    "rally": Decimal("0.45"),
}

BEARISH_KEYWORDS: dict[str, Decimal] = {
    "hack": Decimal("0.95"),
    "hacked": Decimal("0.95"),
    "exploit": Decimal("0.95"),
    "exploited": Decimal("0.95"),
    "insolvent": Decimal("0.95"),
    "insolvency": Decimal("0.95"),
    "bankruptcy": Decimal("0.95"),
    "bankrupt": Decimal("0.95"),
    "sec lawsuit": Decimal("0.85"),
    "lawsuit": Decimal("0.70"),
    "subpoena": Decimal("0.80"),
    "indictment": Decimal("0.85"),
    "arrested": Decimal("0.85"),
    "fraud": Decimal("0.90"),
    "scam": Decimal("0.85"),
    "ponzi": Decimal("0.90"),
    "ban": Decimal("0.80"),
    "banned": Decimal("0.80"),
    "crackdown": Decimal("0.75"),
    "rate hike": Decimal("0.75"),
    "depeg": Decimal("0.90"),
    "depegged": Decimal("0.90"),
    "outage": Decimal("0.80"),
    "freeze": Decimal("0.80"),
    "halted": Decimal("0.75"),
    "crash": Decimal("0.65"),
    "plunge": Decimal("0.60"),
    "dump": Decimal("0.60"),
    "bearish": Decimal("0.50"),
}

NEGATION_TERMS: set[str] = {
    "not",
    "no",
    "never",
    "false",
    "denies",
    "denied",
    "dismisses",
    "untrue",
    "neither",
}


class NewsSentimentAnalyzer:
    """Deterministic, zero-dependency financial sentiment and volatility impact classifier.

    Guarantees:
    - Pure Decimal scores bounded between -1.00 and +1.00.
    - Negation-aware keyword extraction (e.g. 'not hacked' will not trigger critical bearish score).
    - Sub-millisecond evaluation latency.
    """

    def __init__(
        self,
        bullish_lexicon: dict[str, Decimal] | None = None,
        bearish_lexicon: dict[str, Decimal] | None = None,
    ) -> None:
        self.bullish_lexicon = bullish_lexicon or dict(BULLISH_KEYWORDS)
        self.bearish_lexicon = bearish_lexicon or dict(BEARISH_KEYWORDS)

    def analyze_headline(
        self,
        headline: str,
        now: datetime | None = None,
    ) -> tuple[Decimal, list[str], NewsImpactLevel]:
        """Analyzes headline text and returns (sentiment_score, matched_keywords, impact_level)."""
        clean_text = headline.lower().strip()
        tokens = re.findall(r"\b[\w'-]+\b", clean_text)

        matched_keywords: list[str] = []
        raw_score = ZERO_DECIMAL

        # 1. Match multi-word phrases first
        for phrase, weight in self.BULLISH_PHRASES():
            if phrase in clean_text:
                if not self._is_negated_phrase(phrase, tokens):
                    raw_score += weight
                    matched_keywords.append(phrase)
                else:
                    raw_score -= weight * Decimal("0.5")
                    matched_keywords.append(f"negated_{phrase}")

        for phrase, weight in self.BEARISH_PHRASES():
            if phrase in clean_text:
                if not self._is_negated_phrase(phrase, tokens):
                    raw_score -= weight
                    matched_keywords.append(phrase)
                else:
                    raw_score += weight * Decimal("0.5")
                    matched_keywords.append(f"negated_{phrase}")

        # 2. Match single-word keywords
        for idx, token in enumerate(tokens):
            is_negated = idx > 0 and tokens[idx - 1] in NEGATION_TERMS
            if idx > 1 and tokens[idx - 2] in NEGATION_TERMS:
                is_negated = True

            if token in self.bullish_lexicon and token not in clean_text:
                continue

            if token in self.bullish_lexicon:
                weight = self.bullish_lexicon[token]
                if is_negated:
                    raw_score -= weight * Decimal("0.5")
                    matched_keywords.append(f"negated_{token}")
                else:
                    raw_score += weight
                    matched_keywords.append(token)

            elif token in self.bearish_lexicon:
                weight = self.bearish_lexicon[token]
                if is_negated:
                    raw_score += weight * Decimal("0.5")
                    matched_keywords.append(f"negated_{token}")
                else:
                    raw_score -= weight
                    matched_keywords.append(token)

        # 3. Normalize score into [-1.00, +1.00]
        final_score = self._clamp_score(raw_score)
        impact = self._determine_impact(final_score, matched_keywords)
        return final_score, matched_keywords, impact

    def analyze_article(
        self,
        article: NewsArticle,
        now: datetime | None = None,
    ) -> NewsSentimentResult:
        """Evaluates an incoming NewsArticle and builds a NewsSentimentResult."""
        current_time = now or datetime.now(UTC)
        score, keywords, impact = self.analyze_headline(article.title, now=current_time)

        # Classify sentiment type
        if score >= Decimal("0.20"):
            sentiment = NewsSentimentType.BULLISH
        elif score <= Decimal("-0.20"):
            sentiment = NewsSentimentType.BEARISH
        else:
            sentiment = NewsSentimentType.NEUTRAL

        # Confidence is derived from count of matched keywords
        confidence = min(Decimal(len(keywords)) * Decimal("0.35"), Decimal("1.00"))
        if not keywords:
            confidence = Decimal("0.10")

        return NewsSentimentResult(
            article_id=article.article_id,
            headline=article.title,
            sentiment=sentiment,
            sentiment_score=score,
            impact=impact,
            matched_keywords=keywords,
            confidence=confidence,
            evaluated_at=current_time,
        )

    def BULLISH_PHRASES(self) -> list[tuple[str, Decimal]]:
        return [(p, w) for p, w in self.bullish_lexicon.items() if " " in p or "-" in p]

    def BEARISH_PHRASES(self) -> list[tuple[str, Decimal]]:
        return [(p, w) for p, w in self.bearish_lexicon.items() if " " in p or "-" in p]

    def _is_negated_phrase(self, phrase: str, tokens: list[str]) -> bool:
        phrase_words = phrase.split()
        if not phrase_words:
            return False
        first_word = phrase_words[0]
        try:
            idx = tokens.index(first_word)
            if idx > 0 and tokens[idx - 1] in NEGATION_TERMS:
                return True
            if idx > 1 and tokens[idx - 2] in NEGATION_TERMS:
                return True
        except ValueError:
            pass
        return False

    @staticmethod
    def _clamp_score(val: Decimal) -> Decimal:
        if val > Decimal("1.00"):
            return Decimal("1.00")
        if val < Decimal("-1.00"):
            return Decimal("-1.00")
        return round(val, 2)

    @staticmethod
    def _determine_impact(score: Decimal, keywords: list[str]) -> NewsImpactLevel:
        abs_score = abs(score)
        critical_terms = {"hack", "hacked", "exploit", "insolvent", "bankruptcy", "depeg"}
        if any(k in critical_terms for k in keywords) or abs_score >= Decimal("0.85"):
            return NewsImpactLevel.CRITICAL
        if abs_score >= Decimal("0.50"):
            return NewsImpactLevel.HIGH
        if abs_score >= Decimal("0.20"):
            return NewsImpactLevel.MEDIUM
        return NewsImpactLevel.LOW
