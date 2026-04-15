"""Catalyst and score evolution API routes.

Endpoints:
  GET  /catalyst/{ticker}              — full catalyst breakdown for a ticker
  POST /catalyst/{ticker}/refresh      — re-fetch catalyst events for a ticker
  GET  /catalyst/regime-changes        — recent regime change events across universe
  GET  /evolution/{ticker}             — score trajectory history for a ticker
  GET  /evolution/alerts               — tickers with active regime change events
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.asset import Asset, AssetEvent, AssetScoreDaily

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Catalyst endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{ticker}", summary="Full catalyst breakdown for a ticker")
def get_catalyst(ticker: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    ticker = ticker.upper()
    asset = db.execute(select(Asset).where(Asset.ticker == ticker)).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found. Ingest it first.")

    events = (
        db.query(AssetEvent)
        .filter(AssetEvent.asset_id == asset.id)
        .order_by(AssetEvent.event_date.desc())
        .all()
    )

    from app.services.catalyst.aggregator import compute_full_catalyst
    event_dicts = [
        {
            'event_type': e.event_type,
            'event_date': e.event_date,
            'title': e.title,
            'summary': e.summary,
            'sentiment_score': e.sentiment_score,
            'importance_score': e.importance_score,
            'source': e.source,
        }
        for e in events
    ]
    catalyst = compute_full_catalyst(ticker, event_dicts)

    # Latest score
    latest_score = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == asset.id)
        .order_by(AssetScoreDaily.date.desc())
        .first()
    )

    return {
        'ticker': ticker,
        'asset_name': asset.name,
        'catalyst': {
            'score': catalyst.score,
            'type': catalyst.catalyst_type,
            'qualifies_as_filter': catalyst.qualifies_as_filter,
            'description': catalyst.description,
            'components': {
                'earnings': catalyst.earnings_score,
                'insider': catalyst.insider_score,
                'news': catalyst.news_score,
            },
        },
        'events': [
            {
                'type': e.event_type,
                'date': str(e.event_date)[:10],
                'title': e.title,
                'sentiment': e.sentiment_score,
                'importance': e.importance_score,
                'source': e.source,
            }
            for e in events[:20]
        ],
        'latest_score': {
            'total': latest_score.total_score if latest_score else None,
            'percentile': latest_score.score_percentile if latest_score else None,
            'regime': latest_score.score_regime if latest_score else None,
            'trajectory': latest_score.score_trajectory if latest_score else None,
        } if latest_score else None,
    }


@router.post("/{ticker}/refresh", summary="Re-fetch catalyst events for a ticker")
def refresh_catalyst(ticker: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    ticker = ticker.upper()
    asset = db.execute(select(Asset).where(Asset.ticker == ticker)).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found.")

    from app.services.catalyst.aggregator import fetch_and_compute_catalyst
    events, catalyst = fetch_and_compute_catalyst(ticker)

    # Upsert events
    from app.models.asset import AssetEvent as AE
    db.query(AE).filter(AE.asset_id == asset.id).delete()
    for row in events:
        db.add(AE(asset_id=asset.id, **row))
    db.commit()

    return {
        'ticker': ticker,
        'events_stored': len(events),
        'catalyst_score': catalyst.score,
        'catalyst_type': catalyst.catalyst_type,
        'qualifies_as_filter': catalyst.qualifies_as_filter,
    }


@router.get("/regime-changes/recent", summary="Recent regime change events across universe")
def get_regime_changes(
    days: int = 7,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    events = (
        db.query(AssetEvent, Asset.ticker, Asset.name)
        .join(Asset, Asset.id == AssetEvent.asset_id)
        .filter(
            AssetEvent.event_type == 'regime_change',
            AssetEvent.event_date >= cutoff,
        )
        .order_by(AssetEvent.event_date.desc())
        .limit(50)
        .all()
    )

    return {
        'count': len(events),
        'days': days,
        'changes': [
            {
                'ticker': ticker,
                'name': name,
                'date': str(e.event_date)[:10],
                'title': e.title,
                'summary': e.summary,
                'sentiment': e.sentiment_score,
                'importance': e.importance_score,
            }
            for e, ticker, name in events
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Evolution endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/evolution/{ticker}", summary="Score trajectory history for a ticker")
def get_evolution(
    ticker: str,
    days: int = 60,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ticker = ticker.upper()
    asset = db.execute(select(Asset).where(Asset.ticker == ticker)).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found.")

    cutoff = date.today() - timedelta(days=days)
    rows = (
        db.query(AssetScoreDaily)
        .filter(
            AssetScoreDaily.asset_id == asset.id,
            AssetScoreDaily.date >= cutoff,
        )
        .order_by(AssetScoreDaily.date.asc())
        .all()
    )

    if not rows:
        raise HTTPException(status_code=404, detail="No score history found for this ticker.")

    latest = rows[-1]

    history = [
        {
            'date': str(r.date),
            'total_score': r.total_score,
            'percentile': r.score_percentile,
            'state': r.state,
            'trajectory': r.score_trajectory,
            'regime': r.score_regime,
            'slope_5d': r.score_slope_5d,
            'slope_20d': r.score_slope_20d,
            'growth': r.growth_score,
            'quality': r.quality_score,
            'market': r.market_score,
            'narrative': r.narrative_score,
        }
        for r in rows
    ]

    return {
        'ticker': ticker,
        'asset_name': asset.name,
        'current': {
            'date': str(latest.date),
            'total_score': latest.total_score,
            'percentile': latest.score_percentile,
            'regime': latest.score_regime,
            'trajectory': latest.score_trajectory,
            'slope_5d': latest.score_slope_5d,
            'slope_20d': latest.score_slope_20d,
        },
        'history': history,
    }


@router.get("/evolution/alerts/active", summary="Tickers with active regime change or breakout")
def get_evolution_alerts(
    days: int = 3,
    regimes: str = "STRONG_UPTREND,UPTREND,TOPPING",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Return tickers that recently changed to notable regimes.
    `regimes` is a comma-separated list of regime names to filter by.
    """
    target_regimes = [r.strip() for r in regimes.split(',') if r.strip()]
    cutoff = date.today() - timedelta(days=days)

    rows = (
        db.query(AssetScoreDaily, Asset.ticker, Asset.name, Asset.sector)
        .join(Asset, Asset.id == AssetScoreDaily.asset_id)
        .filter(
            AssetScoreDaily.date >= cutoff,
            AssetScoreDaily.score_regime.in_(target_regimes),
        )
        .order_by(AssetScoreDaily.score_percentile.desc().nullslast())
        .limit(50)
        .all()
    )

    return {
        'count': len(rows),
        'regimes_filter': target_regimes,
        'days': days,
        'alerts': [
            {
                'ticker': ticker,
                'name': name,
                'sector': sector,
                'date': str(row.date),
                'regime': row.score_regime,
                'trajectory': row.score_trajectory,
                'total_score': row.total_score,
                'percentile': row.score_percentile,
                'slope_5d': row.score_slope_5d,
            }
            for row, ticker, name, sector in rows
        ],
    }
