"""Earnings catalyst engine.

Fetches:
  - Historical EPS actuals vs estimates (surprise % calculation)
  - Upcoming earnings dates + consensus estimate
  - Beat/miss streak tracking

Data source: yfinance (free, no API key)
Reliability: HIGH for dates and actuals; MODERATE for estimates
             (yfinance sourced from Yahoo Finance consensus)

Surprise formula:
  surprise_pct = (actual - estimate) / abs(estimate) * 100
  Positive = beat, Negative = miss

Importance scoring (0-100):
  - Beat > 10%  → 90
  - Beat 5-10%  → 75
  - Beat 0-5%   → 60
  - Miss 0-5%   → 40
  - Miss > 5%   → 25
  - Miss > 15%  → 10

Sentiment scoring (-1 to +1):
  - Importance scaled, direction from beat/miss
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Importance / sentiment helpers
# ─────────────────────────────────────────────────────────────────────────────

def _surprise_to_importance(surprise_pct: float | None) -> float:
    if surprise_pct is None:
        return 50.0
    a = abs(surprise_pct)
    if a >= 20:
        return 90.0
    if a >= 10:
        return 80.0
    if a >= 5:
        return 70.0
    return 55.0


def _surprise_to_sentiment(surprise_pct: float | None) -> float:
    """Map EPS surprise to sentiment score -1..+1."""
    if surprise_pct is None:
        return 0.0
    if surprise_pct >= 15:
        return 0.85
    if surprise_pct >= 10:
        return 0.70
    if surprise_pct >= 5:
        return 0.50
    if surprise_pct >= 0:
        return 0.25
    if surprise_pct >= -5:
        return -0.25
    if surprise_pct >= -15:
        return -0.55
    return -0.80


def _beat_miss_streak(surprises: list[float | None]) -> int:
    """
    Return the current beat/miss streak.
    Positive = consecutive beats, Negative = consecutive misses.
    Most recent first.
    """
    streak = 0
    direction: int | None = None
    for s in surprises:
        if s is None:
            break
        d = 1 if s >= 0 else -1
        if direction is None:
            direction = d
        if d != direction:
            break
        streak += d
    return streak


# ─────────────────────────────────────────────────────────────────────────────
# Fetch functions
# ─────────────────────────────────────────────────────────────────────────────

def fetch_earnings_events(ticker: str) -> list[dict[str, Any]]:
    """
    Return a list of AssetEvent-compatible dicts from historical EPS data.

    Each dict has:
        event_type, event_date, title, summary,
        sentiment_score, importance_score, source, external_id
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
    except ImportError:
        logger.warning("yfinance not installed — earnings engine unavailable")
        return []

    events: list[dict[str, Any]] = []

    # ── Historical EPS surprises ──────────────────────────────────────────────
    try:
        eh = tk.earnings_history
        if eh is not None and not getattr(eh, 'empty', True):
            eh_reset = eh.reset_index() if hasattr(eh, 'reset_index') else eh
            surprises: list[float | None] = []

            for _, row in eh_reset.iterrows():
                try:
                    # Column names vary by yfinance version
                    date_col = next(
                        (c for c in row.index if 'date' in str(c).lower()), None
                    )
                    actual_col = next(
                        (c for c in row.index if 'actual' in str(c).lower()), None
                    )
                    estimate_col = next(
                        (c for c in row.index if 'estimate' in str(c).lower()), None
                    )

                    if not date_col:
                        continue

                    raw_date = row[date_col]
                    if hasattr(raw_date, 'to_pydatetime'):
                        event_dt = raw_date.to_pydatetime()
                    elif isinstance(raw_date, datetime):
                        event_dt = raw_date
                    else:
                        event_dt = datetime.strptime(str(raw_date)[:10], '%Y-%m-%d')

                    if event_dt.tzinfo is None:
                        event_dt = event_dt.replace(tzinfo=timezone.utc)

                    actual = float(row[actual_col]) if actual_col and row[actual_col] is not None else None
                    estimate = float(row[estimate_col]) if estimate_col and row[estimate_col] is not None else None

                    surprise_pct: float | None = None
                    if actual is not None and estimate not in (None, 0):
                        surprise_pct = ((actual - estimate) / abs(estimate)) * 100.0

                    surprises.append(surprise_pct)
                    beat_label = (
                        f"Beat by {surprise_pct:.1f}%" if surprise_pct is not None and surprise_pct >= 0
                        else f"Missed by {abs(surprise_pct):.1f}%" if surprise_pct is not None
                        else "EPS reported"
                    )

                    events.append({
                        'event_type': 'earnings_result',
                        'event_date': event_dt,
                        'title': f'Earnings: {beat_label}',
                        'summary': (
                            f"EPS actual={actual:.2f} estimate={estimate:.2f} "
                            f"surprise={surprise_pct:.1f}%"
                            if actual is not None and estimate is not None and surprise_pct is not None
                            else f"EPS reported: actual={actual}"
                        ),
                        'sentiment_score': _surprise_to_sentiment(surprise_pct),
                        'importance_score': _surprise_to_importance(surprise_pct),
                        'source': 'yfinance_earnings',
                        'external_id': f"earnings_{ticker}_{str(raw_date)[:10]}",
                        '_surprise_pct': surprise_pct,  # internal, stripped before DB insert
                    })
                except Exception as row_exc:
                    logger.debug("Skipping earnings row: %s", row_exc)
                    continue

            # Annotate streak on the most recent event
            if events and surprises:
                streak = _beat_miss_streak(surprises)
                if streak != 0:
                    events[0]['summary'] += (
                        f" | Beat streak: {streak} quarters"
                        if streak > 0
                        else f" | Miss streak: {abs(streak)} quarters"
                    )
    except Exception as exc:
        logger.warning("Earnings history fetch failed for %s: %s", ticker, exc)

    # ── Upcoming earnings date ────────────────────────────────────────────────
    try:
        cal = tk.calendar
        if cal is not None and not getattr(cal, 'empty', True):
            now = datetime.now(tz=timezone.utc)
            # calendar can be a DataFrame or dict depending on yfinance version
            if hasattr(cal, 'loc'):
                for label in ('Earnings Date', 'Earnings High', 'Earnings Low'):
                    if label in cal.index:
                        val = cal.loc[label]
                        raw = val.iloc[0] if hasattr(val, 'iloc') else val
                        if raw is not None:
                            if hasattr(raw, 'to_pydatetime'):
                                event_dt = raw.to_pydatetime()
                            else:
                                event_dt = raw
                            if hasattr(event_dt, 'tzinfo') and event_dt.tzinfo is None:
                                event_dt = event_dt.replace(tzinfo=timezone.utc)
                            if event_dt > now:
                                events.append({
                                    'event_type': 'earnings_upcoming',
                                    'event_date': event_dt,
                                    'title': 'Upcoming earnings release',
                                    'summary': 'Next scheduled earnings date from yfinance calendar.',
                                    'sentiment_score': 0.1,
                                    'importance_score': 72.0,
                                    'source': 'yfinance_calendar',
                                    'external_id': f"upcoming_{ticker}_{str(event_dt)[:10]}",
                                })
                            break
    except Exception as exc:
        logger.debug("Upcoming earnings fetch failed for %s: %s", ticker, exc)

    # Strip internal fields before returning
    for e in events:
        e.pop('_surprise_pct', None)

    logger.debug("Earnings events for %s: %d", ticker, len(events))
    return events


def compute_earnings_catalyst_score(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate earnings events into a single catalyst score.

    Returns:
        score        : 0-100 (higher = stronger positive earnings catalyst)
        direction    : 'beat' | 'miss' | 'neutral'
        recency_days : days since last earnings
        description  : human-readable summary
    """
    result_events = [e for e in events if e.get('event_type') == 'earnings_result']
    if not result_events:
        return {'score': 50.0, 'direction': 'neutral', 'recency_days': None, 'description': 'No earnings history.'}

    now = datetime.now(tz=timezone.utc)
    sorted_events = sorted(result_events, key=lambda e: e['event_date'], reverse=True)
    latest = sorted_events[0]

    dt = latest['event_date']
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    recency_days = (now - dt).days

    # Recency weight: full weight if < 30 days, decays to 0.5 at 90 days
    recency_weight = max(0.5, 1.0 - recency_days / 180)

    base_score = latest.get('importance_score', 50.0)
    direction_multiplier = 1.0 if (latest.get('sentiment_score') or 0) >= 0 else -1.0
    direction = 'beat' if direction_multiplier > 0 else 'miss'

    score = 50.0 + direction_multiplier * (base_score - 50.0) * recency_weight

    # Boost if beat streak (encoded in summary)
    if 'Beat streak' in (latest.get('summary') or ''):
        score = min(100.0, score + 5.0)

    return {
        'score': round(max(0.0, min(100.0, score)), 2),
        'direction': direction,
        'recency_days': recency_days,
        'description': latest.get('title', ''),
    }
