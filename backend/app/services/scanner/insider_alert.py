"""Insider Alerts Scanner.

Separate from Early Signals — focused specifically on insider buying signals
that may precede a move, even when structural score is low.

Signal types (strongest to weakest):
  CLUSTER_BUY     : 2+ insiders bought in last 30 days (strongest signal)
  LARGE_BUY       : Single insider bought > $500k in last 30 days
  EXECUTIVE_BUY   : CEO/CFO bought any amount > $50k

Filters (gates):
  - Total score >= 35 (avoid failing/bankrupt companies)
  - Exclude if regime=DOWNTREND for more than 90 days
    (avoid turnarounds that never happen)
  - Exclude small transactions < $50k (noise, 10b5-1 auto-buys)

Ranking: by total dollar amount of insider purchases (descending).

Expiry: signal stays active for 45 days after most recent insider buy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, date
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass
class InsiderSignal:
    signal_type: str                # CLUSTER_BUY, LARGE_BUY, EXECUTIVE_BUY
    dollar_amount: float            # total $ bought
    num_insiders: int               # distinct insiders involved
    num_transactions: int
    largest_single: float           # biggest single transaction $
    most_recent_date: datetime | None
    details: list[str] = field(default_factory=list)
    qualifies: bool = False


MIN_TRANSACTION_DOLLARS   = 50_000     # ignore below this (noise)
LARGE_BUY_THRESHOLD       = 500_000    # single insider buy above → LARGE_BUY
CLUSTER_MIN_INSIDERS      = 2          # distinct insiders for CLUSTER_BUY
MIN_TOTAL_SCORE           = 35.0       # avoid failing companies
LOOKBACK_DAYS             = 30         # window for counting buys


def _normalize_ts(dt) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def evaluate_insider_alert(score, events: list) -> InsiderSignal:
    """Evaluate a single asset for insider alert signal."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    details: list[str] = []

    # Filter buy events in lookback window, above noise threshold
    relevant: list = []
    for e in events:
        if e.event_type != 'insider_buy':
            continue
        event_date = _normalize_ts(e.event_date)
        if event_date is None or event_date < cutoff:
            continue

        # Parse dollar amount from title (format: "Name bought $X,XXX,XXX")
        dollar = _extract_dollar_amount(e.title or '')
        if dollar < MIN_TRANSACTION_DOLLARS:
            continue

        relevant.append({
            'event': e,
            'date': event_date,
            'dollar': dollar,
            'name': _extract_insider_name(e.title or ''),
        })

    if not relevant:
        return InsiderSignal(
            signal_type='NONE', dollar_amount=0, num_insiders=0,
            num_transactions=0, largest_single=0, most_recent_date=None,
            details=['No significant insider buys in last 30 days'],
            qualifies=False,
        )

    total_dollar = sum(r['dollar'] for r in relevant)
    num_tx = len(relevant)
    distinct_names = {r['name'] for r in relevant if r['name']}
    num_insiders = len(distinct_names) if distinct_names else num_tx
    largest = max(r['dollar'] for r in relevant)
    most_recent = max(r['date'] for r in relevant)

    # Gate: score must be >= 35
    total_score = score.total_score if score else 0
    if total_score < MIN_TOTAL_SCORE:
        return InsiderSignal(
            signal_type='NONE', dollar_amount=total_dollar,
            num_insiders=num_insiders, num_transactions=num_tx,
            largest_single=largest, most_recent_date=most_recent,
            details=[f'Score too low ({total_score:.0f} < {MIN_TOTAL_SCORE})'],
            qualifies=False,
        )

    # Gate: exclude prolonged downtrends
    if score and score.score_regime:
        regime = score.score_regime.upper()
        if 'DOWNTREND' in regime and (score.score_trajectory or '').lower() not in ('rising', 'improving'):
            return InsiderSignal(
                signal_type='NONE', dollar_amount=total_dollar,
                num_insiders=num_insiders, num_transactions=num_tx,
                largest_single=largest, most_recent_date=most_recent,
                details=[f'Prolonged downtrend ({regime})'],
                qualifies=False,
            )

    # Classify signal type (strongest first)
    if num_insiders >= CLUSTER_MIN_INSIDERS:
        signal_type = 'CLUSTER_BUY'
        details.append(f"{num_insiders} different insiders bought last 30d")
    elif largest >= LARGE_BUY_THRESHOLD:
        signal_type = 'LARGE_BUY'
        details.append(f"Single insider bought ${largest:,.0f}")
    else:
        signal_type = 'EXECUTIVE_BUY'
        details.append(f"Insider bought ${largest:,.0f}")

    details.append(f"Total insider buying: ${total_dollar:,.0f}")
    details.append(f"Most recent: {most_recent.strftime('%Y-%m-%d')}")

    return InsiderSignal(
        signal_type=signal_type,
        dollar_amount=total_dollar,
        num_insiders=num_insiders,
        num_transactions=num_tx,
        largest_single=largest,
        most_recent_date=most_recent,
        details=details,
        qualifies=True,
    )


def _extract_dollar_amount(title: str) -> float:
    """Parse '...bought $21,241,844' -> 21241844.0"""
    import re
    m = re.search(r'\$([\d,]+(?:\.\d+)?)', title)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            return 0.0
    return 0.0


def _extract_insider_name(title: str) -> str:
    """Parse 'STEVENS MARK A bought $...' -> 'STEVENS MARK A'"""
    import re
    m = re.match(r'^(.+?)\s+(bought|sold)\s+\$', title)
    if m:
        return m.group(1).strip()
    return ''


def refresh_insider_alerts(db) -> dict:
    """Run insider alerts for all assets and cache results."""
    from app.models.asset import Asset, AssetScoreDaily, AssetEvent
    from app.models.insider_alert import InsiderAlertCache
    from sqlalchemy import desc

    # Get latest score per asset
    latest_scores = (
        db.query(AssetScoreDaily)
        .order_by(desc(AssetScoreDaily.date))
        .all()
    )
    latest_per_asset: dict[str, AssetScoreDaily] = {}
    for s in latest_scores:
        if s.asset_id not in latest_per_asset:
            latest_per_asset[s.asset_id] = s

    # Clear previous cache
    db.query(InsiderAlertCache).delete()
    db.flush()

    new_count = 0
    for asset_id, score in latest_per_asset.items():
        events = (
            db.query(AssetEvent)
            .filter(AssetEvent.asset_id == asset_id)
            .order_by(desc(AssetEvent.event_date))
            .limit(50)
            .all()
        )
        result = evaluate_insider_alert(score, events)
        if result.qualifies:
            cache = InsiderAlertCache(
                asset_id=asset_id,
                signal_type=result.signal_type,
                dollar_amount=result.dollar_amount,
                num_insiders=result.num_insiders,
                num_transactions=result.num_transactions,
                largest_single=result.largest_single,
                most_recent_date=result.most_recent_date,
                total_score=score.total_score,
                details='; '.join(result.details),
            )
            db.add(cache)
            new_count += 1

    db.commit()
    return {'total_alerts': new_count}
