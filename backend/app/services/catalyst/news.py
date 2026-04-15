"""News scoring engine.

Data source: yfinance ticker.news (free, no API key)
Backup: RSS feeds from Yahoo Finance / Reuters (configurable)

Scoring methodology:
  1. Fetch recent headlines (typically 10-20 per ticker from yfinance)
  2. Score each headline with a weighted keyword dictionary
  3. Apply recency decay (half-life = 7 days)
  4. Aggregate into a composite sentiment score (-1 to +1) and
     normalised catalyst score (0-100)

Keyword dictionary
------------------
Grouped by financial context category, with empirically-tuned weights.
Bullish weight > 0, bearish weight < 0.

The dictionary is intentionally financial-domain specific — general
sentiment libraries (VADER, TextBlob) score financial text poorly
because "beat", "kill", "crush" are positive in earnings context.

Reliability: MODERATE
  - Headlines are reliable signals for narrative catalysts
  - Sentiment is shallow (no entity linking, no negation deep-parse)
  - Best used as a tiebreaker, not a primary signal
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Keyword dictionary  (word/phrase → sentiment weight)
# ─────────────────────────────────────────────────────────────────────────────

_KEYWORDS: dict[str, float] = {
    # ── Strong bullish ──────────────────────────────────────────────────────
    'beat expectations': 1.0,
    'beat estimates': 1.0,
    'blowout': 1.0,
    'record revenue': 0.9,
    'record earnings': 0.9,
    'all-time high': 0.8,
    'raises guidance': 0.9,
    'raised guidance': 0.9,
    'raising guidance': 0.9,
    'accelerating growth': 0.8,
    'strong demand': 0.7,
    'major contract': 0.8,
    'landmark deal': 0.8,
    'breakthrough': 0.7,
    'fda approval': 0.9,
    'fda approved': 0.9,
    'buyback': 0.6,
    'share repurchase': 0.6,
    'dividend increase': 0.7,
    'upgraded': 0.6,
    'upgrade': 0.5,
    'price target raised': 0.8,
    'outperform': 0.5,
    'overweight': 0.5,
    'buy rating': 0.6,
    'strategic acquisition': 0.5,
    'partnership': 0.4,
    'market share gain': 0.7,
    'profitability': 0.5,
    'profitable': 0.5,
    'cash flow positive': 0.7,
    'expanding margins': 0.6,
    'margin expansion': 0.6,

    # ── Moderate bullish ────────────────────────────────────────────────────
    'beat': 0.4,
    'tops': 0.3,
    'surpasses': 0.4,
    'exceeds': 0.4,
    'growth': 0.2,
    'rally': 0.3,
    'surge': 0.4,
    'soars': 0.5,
    'rises': 0.2,
    'gains': 0.2,
    'jumps': 0.3,

    # ── Strong bearish ──────────────────────────────────────────────────────
    'misses estimates': -1.0,
    'missed estimates': -1.0,
    'misses expectations': -1.0,
    'lowers guidance': -0.9,
    'lowered guidance': -0.9,
    'cuts guidance': -0.9,
    'profit warning': -0.9,
    'revenue miss': -0.9,
    'earnings miss': -0.9,
    'disappointing': -0.7,
    'fda rejection': -0.9,
    'rejected by fda': -0.9,
    'class action': -0.8,
    'sec investigation': -0.9,
    'sec charges': -0.9,
    'accounting fraud': -1.0,
    'restatement': -0.9,
    'going concern': -1.0,
    'bankruptcy': -1.0,
    'layoffs': -0.5,
    'job cuts': -0.5,
    'downgraded': -0.6,
    'downgrade': -0.6,
    'price target cut': -0.8,
    'underperform': -0.5,
    'underweight': -0.5,
    'sell rating': -0.7,
    'margin compression': -0.6,
    'declining revenue': -0.7,
    'revenue decline': -0.7,
    'cash burn': -0.6,
    'dilutive': -0.5,

    # ── Moderate bearish ────────────────────────────────────────────────────
    'miss': -0.35,
    'falls': -0.25,
    'drops': -0.25,
    'slips': -0.2,
    'declines': -0.2,
    'concern': -0.2,
    'warning': -0.3,
    'risk': -0.15,
    'uncertainty': -0.2,
    'volatile': -0.1,
    'recall': -0.4,
}

# Pre-compile sorted by length (longest first, to match phrases before words)
_SORTED_KEYWORDS = sorted(_KEYWORDS.items(), key=lambda x: len(x[0]), reverse=True)


def _score_text(text: str) -> float:
    """
    Score a single piece of text using the keyword dictionary.
    Returns raw score (unbounded, sum of matched weights).
    """
    lower = text.lower()
    score = 0.0
    matched: set[str] = set()

    for phrase, weight in _SORTED_KEYWORDS:
        if phrase in lower:
            # Only count each word/phrase once
            already_covered = any(
                phrase in m or m in phrase for m in matched if phrase != m
            )
            if not already_covered:
                score += weight
                matched.add(phrase)

    return score


def _normalize_score(raw: float, n_articles: int = 1) -> float:
    """
    Normalise raw score to -1..+1 using a soft-clamp.
    Accounts for the number of articles (more articles = stronger signal).
    """
    if n_articles == 0:
        return 0.0
    # Scale: expected max raw score per article ≈ 3.0
    scaled = raw / (3.0 * max(1, n_articles) ** 0.5)
    # Soft-clamp with tanh
    return round(math.tanh(scaled), 4)


def _recency_weight(publish_ts: int | float | None) -> float:
    """
    Exponential decay weight based on publication timestamp.
    Half-life = 7 days. Returns 0.1..1.0.
    """
    if publish_ts is None:
        return 0.5
    now = datetime.now(tz=timezone.utc).timestamp()
    age_days = (now - float(publish_ts)) / 86400.0
    half_life = 7.0
    weight = 0.5 ** (age_days / half_life)
    return max(0.1, min(1.0, weight))


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + score
# ─────────────────────────────────────────────────────────────────────────────

def fetch_news_events(ticker: str) -> list[dict[str, Any]]:
    """
    Fetch recent news headlines from yfinance and score each one.
    Returns a list of AssetEvent-compatible dicts.
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        raw_news = tk.news or []
    except Exception as exc:
        logger.warning("News fetch failed for %s: %s", ticker, exc)
        return []

    events: list[dict[str, Any]] = []

    for item in raw_news:
        try:
            title = item.get('title') or ''
            summary_text = item.get('summary') or item.get('description') or ''
            publisher = item.get('publisher') or item.get('source') or 'unknown'
            publish_ts = item.get('providerPublishTime') or item.get('published') or None
            url = item.get('link') or item.get('url') or ''
            uuid = item.get('uuid') or item.get('id') or url

            # Score headline + summary (headline weighted 2x)
            raw_score = _score_text(title) * 2.0 + _score_text(summary_text)
            recency = _recency_weight(publish_ts)
            weighted_score = raw_score * recency

            # Convert publish timestamp to datetime
            if publish_ts is not None:
                event_dt = datetime.fromtimestamp(float(publish_ts), tz=timezone.utc)
            else:
                event_dt = datetime.now(tz=timezone.utc)

            # Map to -1..+1 sentiment (each article scored individually)
            norm = _normalize_score(raw_score, n_articles=1)
            # Map to 0..100 importance (based on weighted signal strength)
            importance = min(90.0, 40.0 + abs(weighted_score) * 25.0)

            events.append({
                'event_type': 'news',
                'event_date': event_dt,
                'title': title[:200],
                'summary': f"[{publisher}] {summary_text[:300]}" if summary_text else f"[{publisher}]",
                'sentiment_score': round(norm, 3),
                'importance_score': round(importance, 1),
                'source': f"news_{publisher.lower().replace(' ', '_')[:30]}",
                'external_id': f"news_{ticker}_{str(uuid)[:40]}",
                '_recency_weight': recency,
                '_raw_score': raw_score,
            })
        except Exception as item_exc:
            logger.debug("News item parse error: %s", item_exc)
            continue

    # Strip internal fields
    for e in events:
        e.pop('_recency_weight', None)
        e.pop('_raw_score', None)

    logger.debug("News events for %s: %d articles", ticker, len(events))
    return events


def compute_news_catalyst_score(news_events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate news events into a single catalyst score.

    Returns:
        score       : 0-100 (>50 = positive narrative, <50 = negative)
        sentiment   : -1..+1 aggregate
        article_count: number of articles used
        description : top headline
    """
    articles = [e for e in news_events if e.get('event_type') == 'news']
    if not articles:
        return {
            'score': 50.0,
            'sentiment': 0.0,
            'article_count': 0,
            'description': 'No recent news.',
        }

    now = datetime.now(tz=timezone.utc)

    # Weighted sum of sentiments (recency already baked into events at fetch time)
    # Re-weight by recency based on event_date
    total_weight = 0.0
    weighted_sentiment = 0.0

    for article in articles:
        dt = article.get('event_date')
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (now - dt).total_seconds() / 86400.0
        weight = 0.5 ** (age_days / 7.0)  # 7-day half-life
        weight = max(0.05, weight)

        sentiment = article.get('sentiment_score', 0.0) or 0.0
        weighted_sentiment += sentiment * weight
        total_weight += weight

    aggregate_sentiment = weighted_sentiment / total_weight if total_weight > 0 else 0.0
    aggregate_sentiment = max(-1.0, min(1.0, aggregate_sentiment))

    # Map to 0-100 score
    score = 50.0 + aggregate_sentiment * 40.0

    # Boost if many articles (more coverage = more important catalyst)
    n = min(len(articles), 10)
    score = min(95.0, score + n * 0.5)

    top_article = max(
        articles,
        key=lambda a: abs(a.get('sentiment_score', 0) or 0) * (a.get('importance_score', 0) or 0),
        default=articles[0],
    )

    return {
        'score': round(max(0.0, min(100.0, score)), 2),
        'sentiment': round(aggregate_sentiment, 3),
        'article_count': len(articles),
        'description': top_article.get('title', ''),
    }
