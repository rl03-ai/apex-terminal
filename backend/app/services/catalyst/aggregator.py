"""Catalyst aggregator.

Combines earnings, insider trades, and news signals into a single
CatalystScore that replaces the current narrative_score in the
scoring engine.

Design principles:
  - Earnings = highest weight (hard data, quantifiable)
  - Insider = second (informed decision-makers, legally disclosed)
  - News = third (soft signal, narrative context)
  - Weights shift based on signal availability and recency

CatalystScore → narrative_score in compute_total_score:
  The aggregated score (0-100) feeds directly into the narrative
  component of the scoring engine. When SCORE_NARRATIVE_AS_FILTER=true,
  it is used as an entry gate instead.

Entry filter logic (when SCORE_NARRATIVE_AS_FILTER=true):
  A ticker only qualifies as an 'active_setup' candidate if:
    - structural percentile ≥ 75 (top 25%)
    - AND catalyst_score ≥ 60 (positive catalyst present)
    OR
    - structural percentile ≥ 90 (top 10%) regardless of catalyst

Usage:
    from app.services.catalyst.aggregator import compute_full_catalyst

    events = (earnings_events + insider_events + news_events)
    catalyst = compute_full_catalyst(ticker, events)
    # catalyst.score goes into narrative_score
    # catalyst.type is stored in the explanation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.catalyst.earnings import compute_earnings_catalyst_score
from app.services.catalyst.insider import compute_insider_catalyst_score
from app.services.catalyst.news import compute_news_catalyst_score

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CatalystScore:
    score: float                    # 0-100, feeds into narrative_score
    catalyst_type: str              # dominant catalyst type
    earnings_score: float | None    # 0-100, None if no data
    insider_score: float | None     # 0-100, None if no data
    news_score: float | None        # 0-100, None if no data
    description: str                # human-readable summary
    qualifies_as_filter: bool       # True if passes narrative filter gate


# ─────────────────────────────────────────────────────────────────────────────
# Weights
# ─────────────────────────────────────────────────────────────────────────────

from app.services.catalyst.profiles import CatalystProfile, get_catalyst_profile, CATALYST_PROFILES

_DEFAULT_WEIGHTS = {
    'earnings': 0.45,
    'insider': 0.30,
    'news': 0.25,
}


def _compute_weighted_score(
    earnings: dict | None,
    insider: dict | None,
    news: dict | None,
    profile: CatalystProfile | None = None,
) -> tuple[float, str]:
    """
    Compute weighted average score using sector-specific profile weights.
    Components with no data are excluded and weights are renormalised.
    """
    if profile is None:
        profile = CATALYST_PROFILES['default']

    available: dict[str, tuple[float, float]] = {}  # name → (score, weight)

    if earnings and earnings.get('direction') != 'neutral':
        available['earnings'] = (earnings['score'], profile.w_earnings)
    if insider and insider.get('signal') != 'neutral':
        available['insider'] = (insider['score'], profile.w_insider)
    if news and news.get('article_count', 0) > 0:
        available['news'] = (news['score'], profile.w_news)

    if not available:
        return 50.0, 'none'

    total_weight = sum(w for _, (_, w) in available.items())
    weighted_sum = sum(s * w for _, (s, w) in available.items())
    score = weighted_sum / total_weight

    # Dominant catalyst = highest weighted contribution
    dominant = max(available, key=lambda k: available[k][0] * available[k][1])
    return round(max(0.0, min(100.0, score)), 2), dominant


# ─────────────────────────────────────────────────────────────────────────────
# Full catalyst computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_full_catalyst(
    ticker: str,
    events: list[dict[str, Any]],
    *,
    narrative_filter_threshold: float = 60.0,
    sector: str | None = None,
    industry: str | None = None,
    market_cap: float | None = None,
) -> CatalystScore:
    """
    Compute the CatalystScore for a ticker from its event list.

    Parameters
    ----------
    ticker                     : asset ticker (for logging)
    events                     : combined list of AssetEvent-compatible dicts
    narrative_filter_threshold : min score to qualify as entry catalyst
    sector                     : yfinance sector string (for profile selection)
    industry                   : yfinance industry string (for profile selection)
    market_cap                 : market cap in dollars (for size-based routing)
    """
    # Select sector-specific catalyst profile
    profile = get_catalyst_profile(sector, industry, market_cap, ticker)
    logger.debug("Catalyst profile for %s (%s/%s): %s", ticker, sector, industry, profile.name)

    earnings_result = compute_earnings_catalyst_score(events, profile=profile)
    insider_result = compute_insider_catalyst_score(events, profile=profile)
    news_result = compute_news_catalyst_score(events, profile=profile)

    score, dominant = _compute_weighted_score(
        earnings_result if earnings_result.get('direction') != 'neutral' else None,
        insider_result if insider_result.get('signal') != 'neutral' else None,
        news_result if news_result.get('article_count', 0) > 0 else None,
        profile=profile,
    )

    # Build description from dominant catalyst
    descriptions: list[str] = []
    if earnings_result.get('description'):
        descriptions.append(f"[earnings] {earnings_result['description']}")
    if insider_result.get('signal') not in (None, 'neutral', 'mixed') and insider_result.get('description'):
        descriptions.append(f"[insider] {insider_result['description']}")
    if news_result.get('description'):
        descriptions.append(f"[news] {news_result['description']}")

    profile_note = f'[{profile.name}]'
    description = profile_note + ' ' + (' | '.join(descriptions[:2]) or 'No catalyst data.')
    qualifies = score >= narrative_filter_threshold

    logger.debug(
        "Catalyst %s: score=%.1f type=%s earnings=%.1f insider=%.1f news=%.1f",
        ticker, score, dominant,
        earnings_result.get('score', 50),
        insider_result.get('score', 50),
        news_result.get('score', 50),
    )

    return CatalystScore(
        score=score,
        catalyst_type=dominant,
        earnings_score=earnings_result.get('score'),
        insider_score=insider_result.get('score'),
        news_score=news_result.get('score'),
        description=description,
        qualifies_as_filter=qualifies,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fetch + compute in one call (for the ingestion pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_and_compute_catalyst(ticker: str) -> tuple[list[dict[str, Any]], CatalystScore]:
    """
    Fetch all catalyst events and compute CatalystScore in one call.

    Returns (events_list, catalyst_score).
    events_list is ready for upsert into AssetEvent table.
    """
    from app.services.catalyst.earnings import fetch_earnings_events
    from app.services.catalyst.insider import fetch_insider_events
    from app.services.catalyst.news import fetch_news_events

    earnings_events = fetch_earnings_events(ticker)
    insider_events = fetch_insider_events(ticker)
    news_events = fetch_news_events(ticker)

    all_events = earnings_events + insider_events + news_events
    catalyst = compute_full_catalyst(ticker, all_events)

    return all_events, catalyst


# ─────────────────────────────────────────────────────────────────────────────
# Narrative filter gate
# ─────────────────────────────────────────────────────────────────────────────

def apply_narrative_filter(
    asset_id: str,
    structural_percentile: float,
    catalyst_score: float,
    *,
    top_tier_threshold: float = 90.0,
    filter_threshold: float = 60.0,
    structural_min: float = 75.0,
) -> tuple[bool, str]:
    """
    Determine if a ticker qualifies for 'active_setup' under the narrative filter.

    Returns (qualifies: bool, reason: str).

    Logic:
      - Top tier (≥ p90): qualifies regardless of catalyst
      - Otherwise: requires structural ≥ p75 AND catalyst ≥ 60
    """
    if structural_percentile >= top_tier_threshold:
        return True, f"Top tier structural (p{structural_percentile:.0f})"

    if structural_percentile >= structural_min and catalyst_score >= filter_threshold:
        return True, (
            f"Structural p{structural_percentile:.0f} ≥ {structural_min:.0f} "
            f"AND catalyst {catalyst_score:.1f} ≥ {filter_threshold:.0f}"
        )

    reasons: list[str] = []
    if structural_percentile < structural_min:
        reasons.append(f"Structural p{structural_percentile:.0f} < {structural_min:.0f}")
    if catalyst_score < filter_threshold:
        reasons.append(f"Catalyst {catalyst_score:.1f} < {filter_threshold:.0f}")

    return False, " | ".join(reasons) or "Did not meet filter criteria"
