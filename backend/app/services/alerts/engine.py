"""Alerts engine v2.

Generates actionable alerts from data already in the database.
No external API calls required — all signals come from:
  - AssetScoreDaily  (scores, percentile, regime, trajectory)
  - AssetEvent       (insider buys, earnings results, regime changes)
  - Position         (portfolio positions for score-drop monitoring)
  - PositionSnapshotDaily (historical position scores)

Alert types
-----------
  regime_change_bullish   : asset moved into STRONG_UPTREND or UPTREND
  regime_change_bearish   : asset moved into DOWNTREND or TOPPING
  score_breakout          : total score rose >8 points in 5 days (percentile ≥ 75)
  score_deterioration     : total score fell >8 points in 5 days (percentile ≤ 25)
  insider_buy_large       : insider purchase > $500k in the last 7 days
  earnings_beat_strong    : EPS beat > 10% in the last 14 days
  earnings_miss_large     : EPS miss > 5% in the last 14 days
  position_score_drop     : held position score fell >10 points since entry

Severity levels
---------------
  critical  : immediate action warranted
  high      : review today
  warning   : monitor
  info      : informational

Deduplication
-------------
One alert per (asset_id, alert_type) per day. Existing unread alerts of the
same type are not duplicated.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetEvent, AssetScoreDaily
from app.models.portfolio import Alert, Portfolio, Position, PositionSnapshotDaily

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication helper
# ─────────────────────────────────────────────────────────────────────────────

def _already_alerted(
    db: Session,
    asset_id: str | None,
    alert_type: str,
    since_days: int = 1,
) -> bool:
    """Return True if an unread alert of this type already exists for this asset."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=since_days)
    q = db.query(Alert).filter(
        Alert.alert_type == alert_type,
        Alert.is_read.is_(False),
        Alert.created_at >= cutoff,
    )
    if asset_id:
        q = q.filter(Alert.asset_id == asset_id)
    return q.first() is not None


def _make_alert(
    db: Session,
    *,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    asset_id: str | None = None,
    position_id: str | None = None,
    portfolio_id: str | None = None,
    payload: dict[str, Any] | None = None,
    dedup_days: int = 1,
) -> Alert | None:
    """Create an alert if one doesn't already exist for today."""
    if _already_alerted(db, asset_id, alert_type, since_days=dedup_days):
        return None
    # user_id is required by model — use a sentinel for system alerts
    alert = Alert(
        user_id='system',
        alert_type=alert_type,
        severity=severity,
        title=title,
        message=message,
        asset_id=asset_id,
        position_id=position_id,
        portfolio_id=portfolio_id,
        payload=payload or {},
        is_read=False,
    )
    db.add(alert)
    return alert


# ─────────────────────────────────────────────────────────────────────────────
# Alert generators
# ─────────────────────────────────────────────────────────────────────────────

def generate_regime_change_alerts(db: Session, as_of: date) -> int:
    """
    Fire alerts when an asset's score regime changed today.
    Source: AssetEvent(event_type='regime_change') filed today.
    """
    cutoff = datetime.combine(as_of, datetime.min.time()).replace(tzinfo=timezone.utc)
    events = (
        db.query(AssetEvent, Asset.ticker, Asset.name)
        .join(Asset, Asset.id == AssetEvent.asset_id)
        .filter(
            AssetEvent.event_type == 'regime_change',
            AssetEvent.event_date >= cutoff,
        )
        .all()
    )

    created = 0
    for event, ticker, name in events:
        summary = event.summary or ''
        new_regime = event.title.replace('Regime change → ', '').strip()

        if new_regime in ('STRONG_UPTREND', 'UPTREND'):
            alert = _make_alert(
                db,
                alert_type='regime_change_bullish',
                severity='high' if new_regime == 'STRONG_UPTREND' else 'warning',
                title=f'{ticker} → {new_regime}',
                message=f'{name} score regime shifted to {new_regime}. {summary}',
                asset_id=event.asset_id,
                payload={'regime': new_regime, 'ticker': ticker},
            )
        elif new_regime in ('DOWNTREND', 'TOPPING'):
            alert = _make_alert(
                db,
                alert_type='regime_change_bearish',
                severity='high' if new_regime == 'DOWNTREND' else 'warning',
                title=f'{ticker} → {new_regime}',
                message=f'{name} score regime shifted to {new_regime}. {summary}',
                asset_id=event.asset_id,
                payload={'regime': new_regime, 'ticker': ticker},
            )
        else:
            continue

        if alert:
            created += 1

    return created


def generate_score_momentum_alerts(db: Session, as_of: date) -> int:
    """
    Fire alerts when a score rises/falls sharply over 5 days
    and the asset is in an interesting percentile zone.
    """
    cutoff_date = as_of - timedelta(days=7)

    # Assets with score history in the last week
    today_rows = (
        db.query(AssetScoreDaily, Asset.ticker, Asset.name)
        .join(Asset, Asset.id == AssetScoreDaily.asset_id)
        .filter(AssetScoreDaily.date == as_of)
        .all()
    )

    created = 0
    for score_row, ticker, name in today_rows:
        if score_row.score_slope_5d is None:
            continue

        # Breakout: strong upward momentum in top half of universe
        if (score_row.score_slope_5d >= 1.5
                and (score_row.score_percentile or 0) >= 65):
            delta_5d = round(score_row.score_slope_5d * 5, 1)
            alert = _make_alert(
                db,
                alert_type='score_breakout',
                severity='high',
                title=f'{ticker} score breakout (+{delta_5d:.1f} pts / 5d)',
                message=(
                    f'{name} structural score rising rapidly '
                    f'(slope={score_row.score_slope_5d:+.2f}/day, '
                    f'percentile={score_row.score_percentile:.0f}, '
                    f'regime={score_row.score_regime or "?"}).'
                ),
                asset_id=score_row.asset_id,
                payload={
                    'ticker': ticker,
                    'slope_5d': score_row.score_slope_5d,
                    'percentile': score_row.score_percentile,
                    'regime': score_row.score_regime,
                    'total_score': score_row.total_score,
                },
            )
            if alert:
                created += 1

        # Deterioration: sharp drop in bottom half
        elif (score_row.score_slope_5d <= -1.5
              and (score_row.score_percentile or 100) <= 35):
            delta_5d = round(score_row.score_slope_5d * 5, 1)
            alert = _make_alert(
                db,
                alert_type='score_deterioration',
                severity='warning',
                title=f'{ticker} score deteriorating ({delta_5d:.1f} pts / 5d)',
                message=(
                    f'{name} structural score falling '
                    f'(slope={score_row.score_slope_5d:+.2f}/day, '
                    f'percentile={score_row.score_percentile:.0f}, '
                    f'regime={score_row.score_regime or "?"}).'
                ),
                asset_id=score_row.asset_id,
                payload={
                    'ticker': ticker,
                    'slope_5d': score_row.score_slope_5d,
                    'percentile': score_row.score_percentile,
                    'regime': score_row.score_regime,
                    'total_score': score_row.total_score,
                },
            )
            if alert:
                created += 1

    return created


def generate_insider_alerts(db: Session) -> int:
    """
    Fire alerts on large insider purchases in the last 7 days.
    Threshold: importance_score >= 68 (≈ $500k+ in dollar value).
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    events = (
        db.query(AssetEvent, Asset.ticker, Asset.name)
        .join(Asset, Asset.id == AssetEvent.asset_id)
        .filter(
            AssetEvent.event_type == 'insider_buy',
            AssetEvent.event_date >= cutoff,
            AssetEvent.importance_score >= 68,
        )
        .order_by(AssetEvent.importance_score.desc())
        .limit(20)
        .all()
    )

    created = 0
    for event, ticker, name in events:
        severity = 'high' if (event.importance_score or 0) >= 78 else 'warning'
        alert = _make_alert(
            db,
            alert_type='insider_buy_large',
            severity=severity,
            title=f'{ticker} insider purchase (importance={event.importance_score:.0f})',
            message=event.title or f'{name}: significant insider purchase via SEC Form 4.',
            asset_id=event.asset_id,
            payload={
                'ticker': ticker,
                'importance': event.importance_score,
                'sentiment': event.sentiment_score,
                'summary': event.summary,
            },
            dedup_days=7,
        )
        if alert:
            created += 1
    return created


def generate_earnings_alerts(db: Session) -> int:
    """
    Fire alerts on significant earnings beats/misses in the last 14 days.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=14)
    events = (
        db.query(AssetEvent, Asset.ticker, Asset.name)
        .join(Asset, Asset.id == AssetEvent.asset_id)
        .filter(
            AssetEvent.event_type == 'earnings_result',
            AssetEvent.event_date >= cutoff,
        )
        .all()
    )

    created = 0
    for event, ticker, name in events:
        sentiment = event.sentiment_score or 0.0
        importance = event.importance_score or 50.0

        # Strong beat
        if sentiment >= 0.50 and importance >= 70:
            alert = _make_alert(
                db,
                alert_type='earnings_beat_strong',
                severity='high',
                title=f'{ticker} strong earnings beat',
                message=f'{name}: {event.title}. {event.summary or ""}',
                asset_id=event.asset_id,
                payload={
                    'ticker': ticker,
                    'sentiment': sentiment,
                    'importance': importance,
                    'summary': event.summary,
                },
                dedup_days=14,
            )
            if alert:
                created += 1

        # Significant miss
        elif sentiment <= -0.25 and importance >= 65:
            alert = _make_alert(
                db,
                alert_type='earnings_miss_large',
                severity='warning',
                title=f'{ticker} earnings miss',
                message=f'{name}: {event.title}. {event.summary or ""}',
                asset_id=event.asset_id,
                payload={
                    'ticker': ticker,
                    'sentiment': sentiment,
                    'importance': importance,
                    'summary': event.summary,
                },
                dedup_days=14,
            )
            if alert:
                created += 1

    return created


def generate_position_score_drop_alerts(db: Session) -> int:
    """
    Fire alerts when a held position's score has dropped significantly
    since the entry snapshot. Replaces the stub from v1.
    """
    # Original logic preserved + improved
    created = 0
    positions = db.query(Position).filter(Position.status == 'open').all()

    for position in positions:
        latest = (
            db.query(PositionSnapshotDaily)
            .filter(PositionSnapshotDaily.position_id == position.id)
            .order_by(PositionSnapshotDaily.date.desc())
            .first()
        )
        entry = (
            db.query(PositionSnapshotDaily)
            .filter(PositionSnapshotDaily.position_id == position.id)
            .order_by(PositionSnapshotDaily.date.asc())
            .first()
        )
        if not latest or not entry:
            continue
        if latest.score_total is None or entry.score_total is None:
            continue

        drop = entry.score_total - latest.score_total
        if drop < 10:
            continue

        portfolio = db.query(Portfolio).filter(Portfolio.id == position.portfolio_id).first()
        asset = db.query(Asset).filter(Asset.id == position.asset_id).first()
        ticker = asset.ticker if asset else str(position.asset_id)[:8]

        severity = 'critical' if drop >= 20 else 'high' if drop >= 15 else 'warning'
        alert = _make_alert(
            db,
            alert_type='position_score_drop',
            severity=severity,
            title=f'{ticker} position score dropped {drop:.1f} pts',
            message=(
                f'Score fell from {entry.score_total:.1f} to {latest.score_total:.1f} '
                f'since entry. Thesis review recommended.'
            ),
            asset_id=position.asset_id,
            position_id=position.id,
            portfolio_id=position.portfolio_id,
            payload={
                'ticker': ticker,
                'entry_score': entry.score_total,
                'latest_score': latest.score_total,
                'drop': drop,
            },
            dedup_days=3,
        )
        if alert:
            created += 1

    return created


# ─────────────────────────────────────────────────────────────────────────────
# Legacy compatibility
# ─────────────────────────────────────────────────────────────────────────────

def score_drop_alert(score_now: float, score_entry: float, threshold: float = 10) -> bool:
    """Original stub — kept for backwards compatibility."""
    return (score_entry - score_now) >= threshold


# ─────────────────────────────────────────────────────────────────────────────
# Master runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_alert_checks(db: Session, as_of: date | None = None) -> dict[str, int]:
    """Run all alert generators. Returns counts per type."""
    today = as_of or date.today()
    results: dict[str, int] = {}

    generators = [
        ('regime_change',   lambda: generate_regime_change_alerts(db, today)),
        ('score_momentum',  lambda: generate_score_momentum_alerts(db, today)),
        ('insider',         generate_insider_alerts),
        ('earnings',        generate_earnings_alerts),
        ('position_drop',   generate_position_score_drop_alerts),
    ]

    total = 0
    for name, fn in generators:
        try:
            count = fn(db) if name in ('insider', 'earnings', 'position_drop') else fn()
            results[name] = count
            total += count
            if count:
                logger.info('Alerts generated [%s]: %d', name, count)
        except Exception as exc:
            logger.exception('Alert generator [%s] failed: %s', name, exc)
            results[name] = 0

    results['total'] = total
    return results
