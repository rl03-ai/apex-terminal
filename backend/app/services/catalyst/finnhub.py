"""Finnhub catalyst data provider.

Free tier: 60 requests/minute. Covers 341 tickers in ~6 minutes.

Endpoints used:
  - /stock/earnings          : EPS actual vs estimate, surprise %
  - /calendar/earnings        : upcoming earnings dates
  - /stock/insider-transactions : SEC Form 4 filings
  - /company-news             : company-specific news with sentiment

Docs: https://finnhub.io/docs/api

Fallback strategy:
  - If FINNHUB_API_KEY is not set, functions return empty lists (graceful)
  - If a request fails, caches the failure for 1h to avoid hammering
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
import json

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"
_API_KEY = os.getenv("FINNHUB_API_KEY", "")
_USER_AGENT = "apex-terminal/1.0"

# Rate limiting: 60 req/min = 1 req/sec with some headroom
_MIN_DELAY_SECONDS = 1.1
_last_request_time = 0.0


def _rate_limit() -> None:
    """Sleep if necessary to respect Finnhub rate limit."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    if elapsed < _MIN_DELAY_SECONDS:
        time.sleep(_MIN_DELAY_SECONDS - elapsed)
    _last_request_time = time.monotonic()


def _get(endpoint: str, params: dict[str, Any]) -> Any:
    """Make a GET request to Finnhub API with rate limiting."""
    if not _API_KEY:
        logger.debug("FINNHUB_API_KEY not set, skipping %s", endpoint)
        return None

    _rate_limit()
    params["token"] = _API_KEY
    url = f"{_BASE_URL}{endpoint}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data
    except urllib.error.HTTPError as e:
        if e.code == 429:
            logger.warning("Finnhub rate limit hit on %s, sleeping 60s", endpoint)
            time.sleep(60)
            return None
        if e.code == 403:
            logger.warning("Finnhub 403 on %s - endpoint may require paid plan", endpoint)
            return None
        logger.warning("Finnhub HTTP %d on %s: %s", e.code, endpoint, e.reason)
        return None
    except Exception as e:
        logger.warning("Finnhub error on %s: %s", endpoint, e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Earnings data
# ─────────────────────────────────────────────────────────────────────────────

def fetch_earnings_surprises(ticker: str, limit: int = 4) -> list[dict]:
    """
    Fetch last N quarterly earnings surprises.

    Returns list of AssetEvent-compatible dicts.
    """
    data = _get("/stock/earnings", {"symbol": ticker.upper(), "limit": limit})
    if not data or not isinstance(data, list):
        return []

    events: list[dict] = []
    for row in data:
        actual = row.get("actual")
        estimate = row.get("estimate")
        if actual is None or estimate is None:
            continue

        try:
            surprise_pct = (actual - estimate) / abs(estimate) * 100 if estimate != 0 else 0.0
        except (TypeError, ZeroDivisionError):
            continue

        period = row.get("period")
        if not period:
            continue

        try:
            event_date = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        # Importance 0-100 based on surprise magnitude
        a = abs(surprise_pct)
        importance = 90.0 if a >= 20 else 80.0 if a >= 10 else 70.0 if a >= 5 else 55.0

        # Sentiment -1..+1 based on beat/miss
        if surprise_pct >= 15:      sentiment = 0.85
        elif surprise_pct >= 10:    sentiment = 0.70
        elif surprise_pct >= 5:     sentiment = 0.50
        elif surprise_pct >= 0:     sentiment = 0.25
        elif surprise_pct >= -5:    sentiment = -0.25
        elif surprise_pct >= -15:   sentiment = -0.55
        else:                        sentiment = -0.80

        direction = "beat" if surprise_pct > 0 else "miss"
        events.append({
            "event_type": "earnings_result",
            "event_date": event_date,
            "title": f"EPS {direction} {surprise_pct:+.1f}% ({row.get('quarter','?')}Q)",
            "summary": f"Actual {actual} vs estimate {estimate}",
            "sentiment_score": sentiment,
            "importance_score": importance,
            "source": "finnhub",
        })

    logger.debug("Finnhub earnings for %s: %d events", ticker, len(events))
    return events


def fetch_upcoming_earnings(ticker: str, days_ahead: int = 45) -> list[dict]:
    """Fetch upcoming earnings dates in the next N days."""
    today = datetime.now(tz=timezone.utc).date()
    end_date = today + timedelta(days=days_ahead)

    data = _get("/calendar/earnings", {
        "from":   today.strftime("%Y-%m-%d"),
        "to":     end_date.strftime("%Y-%m-%d"),
        "symbol": ticker.upper(),
    })
    if not data or not isinstance(data, dict):
        return []

    events: list[dict] = []
    for row in data.get("earningsCalendar", []):
        date_str = row.get("date")
        if not date_str:
            continue
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        events.append({
            "event_type": "earnings_upcoming",
            "event_date": event_date,
            "title": f"Upcoming earnings {event_date.date()}",
            "summary": f"Estimate: EPS {row.get('epsEstimate','?')} / Rev {row.get('revenueEstimate','?')}",
            "sentiment_score": 0.0,
            "importance_score": 70.0,
            "source": "finnhub",
        })

    return events


# ─────────────────────────────────────────────────────────────────────────────
# Insider transactions (SEC Form 4)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_insider_transactions(ticker: str, days_back: int = 90) -> list[dict]:
    """
    Fetch insider transactions from the last N days.

    Uses SEC Form 4 filings aggregated by Finnhub.
    Free tier may have limits on lookback window.
    """
    today = datetime.now(tz=timezone.utc).date()
    from_date = today - timedelta(days=days_back)

    data = _get("/stock/insider-transactions", {
        "symbol": ticker.upper(),
        "from":   from_date.strftime("%Y-%m-%d"),
        "to":     today.strftime("%Y-%m-%d"),
    })
    if not data or not isinstance(data, dict):
        return []

    events: list[dict] = []
    for tx in data.get("data", []):
        tx_date_str = tx.get("transactionDate") or tx.get("filingDate")
        if not tx_date_str:
            continue
        try:
            event_date = datetime.strptime(tx_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        shares = tx.get("share", 0) or 0
        change = tx.get("change", 0) or 0
        price = tx.get("transactionPrice", 0) or 0

        # Positive change = buy, Negative = sell
        is_buy = change > 0
        event_type = "insider_buy" if is_buy else "insider_sell"

        dollar_value = abs(change) * price
        # Importance scales with dollar value
        if dollar_value >= 1_000_000:
            importance = 85.0
        elif dollar_value >= 500_000:
            importance = 75.0
        elif dollar_value >= 100_000:
            importance = 65.0
        else:
            importance = 55.0

        name = tx.get("name", "Insider")
        events.append({
            "event_type": event_type,
            "event_date": event_date,
            "title": f"{name} {'bought' if is_buy else 'sold'} ${dollar_value:,.0f}",
            "summary": f"{abs(change):,.0f} shares at ${price:.2f}",
            "sentiment_score": 0.7 if is_buy else -0.3,
            "importance_score": importance,
            "source": "finnhub",
        })

    buys = sum(1 for e in events if e["event_type"] == "insider_buy")
    sells = sum(1 for e in events if e["event_type"] == "insider_sell")
    logger.debug("Finnhub insider %s: %d events (%d buys, %d sells)", ticker, len(events), buys, sells)
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Company news with sentiment
# ─────────────────────────────────────────────────────────────────────────────

def fetch_company_news(ticker: str, days_back: int = 30, max_items: int = 20) -> list[dict]:
    """Fetch company news from the last N days."""
    today = datetime.now(tz=timezone.utc).date()
    from_date = today - timedelta(days=days_back)

    data = _get("/company-news", {
        "symbol": ticker.upper(),
        "from":   from_date.strftime("%Y-%m-%d"),
        "to":     today.strftime("%Y-%m-%d"),
    })
    if not data or not isinstance(data, list):
        return []

    # Score text using our own keyword scoring (same as news.py)
    from app.services.catalyst.news import _score_text, _normalize_score

    events: list[dict] = []
    for item in data[:max_items]:
        ts = item.get("datetime")
        if not ts:
            continue
        try:
            event_date = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            continue

        headline = item.get("headline", "")
        summary  = item.get("summary", "")

        text = f"{headline} {summary}".lower()
        raw_score = _score_text(text)
        sentiment = _normalize_score(raw_score)

        # Skip neutral news
        if abs(sentiment) < 0.05:
            continue

        importance = 60.0 + abs(sentiment) * 25.0

        events.append({
            "event_type": "news",
            "event_date": event_date,
            "title": headline[:160],
            "summary": summary[:300],
            "sentiment_score": sentiment,
            "importance_score": importance,
            "source": "finnhub",
        })

    logger.debug("Finnhub news for %s: %d relevant events", ticker, len(events))
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Combined fetcher for the aggregator
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all_catalyst_events(ticker: str) -> list[dict]:
    """
    Fetch all catalyst events for a ticker in one call.
    Returns combined list ready for AssetEvent upsert.
    """
    if not _API_KEY:
        return []

    events: list[dict] = []

    for fetch_fn, name in [
        (fetch_earnings_surprises,   "earnings"),
        (fetch_upcoming_earnings,    "upcoming"),
        (fetch_insider_transactions, "insider"),
        (fetch_company_news,         "news"),
    ]:
        try:
            fetched = fetch_fn(ticker)
            events.extend(fetched)
        except Exception as exc:
            logger.warning("Finnhub %s fetch failed for %s: %s", name, ticker, exc)

    logger.info("Finnhub total events for %s: %d", ticker, len(events))
    return events


def is_available() -> bool:
    """Check if Finnhub is configured and available."""
    return bool(_API_KEY)
