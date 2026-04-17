from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.asset import Asset, AssetScoreDaily, ScannerResult
from app.schemas.scanner import ScannerResultOut
from app.services.scanner.engine import SCANNER_PROFILES, refresh_all_scanners, refresh_scanner

router = APIRouter()


def _enrich(results: list[ScannerResult], db: Session) -> list[dict[str, Any]]:
    """
    Join scanner results with Asset and latest AssetScoreDaily to add
    ticker, sector, risk_score, market_cap, score_percentile, score_regime.
    """
    if not results:
        return []

    asset_ids = [r.asset_id for r in results]

    assets = {
        a.id: a
        for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()
    }

    # Latest scoring row per asset
    # Use a subquery to get max date per asset, then fetch those rows
    from sqlalchemy import func
    latest_dates = (
        db.query(AssetScoreDaily.asset_id, func.max(AssetScoreDaily.date).label('max_date'))
        .filter(AssetScoreDaily.asset_id.in_(asset_ids))
        .group_by(AssetScoreDaily.asset_id)
        .subquery()
    )
    score_rows = (
        db.query(AssetScoreDaily)
        .join(
            latest_dates,
            (AssetScoreDaily.asset_id == latest_dates.c.asset_id) &
            (AssetScoreDaily.date == latest_dates.c.max_date),
        )
        .all()
    )
    scores = {s.asset_id: s for s in score_rows}

    enriched: list[dict[str, Any]] = []
    for r in results:
        asset = assets.get(r.asset_id)
        score = scores.get(r.asset_id)
        row: dict[str, Any] = {
            'date': r.date,
            'scanner_type': r.scanner_type,
            'rank': r.rank,
            'priority_score': r.priority_score,
            'total_score': r.total_score,
            'state': r.state,
            'why_selected': r.why_selected,
            'asset_id': r.asset_id,
            'ticker': asset.ticker if asset else None,
            'asset_name': asset.name if asset else None,
            'sector': asset.sector if asset else None,
            'market_cap': asset.market_cap if asset else None,
            'risk_score': score.risk_score if score else None,
            'score_percentile': score.score_percentile if score else None,
            'score_regime': score.score_regime if score else None,
            'valuation_score': score.valuation_score if score else None,
        }
        enriched.append(row)
    return enriched


@router.get('/profiles')
def list_scanner_profiles() -> dict[str, dict]:
    return {name: profile.__dict__ for name, profile in SCANNER_PROFILES.items()}


@router.post('/run')
def run_scanners(
    scanner_type: str | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if scanner_type:
        results = refresh_scanner(db, scanner_type=scanner_type, as_of=as_of)
        db.commit()
        return {'scanner_type': scanner_type, 'results': len(results)}
    summary = refresh_all_scanners(db, as_of=as_of)
    db.commit()
    return summary


@router.get('/results', response_model=list[ScannerResultOut])
def get_scanner_results(
    scanner_type: str = Query('repricing'),
    min_score: float = Query(0, ge=0, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.query(ScannerResult)
        .filter(ScannerResult.scanner_type == scanner_type, ScannerResult.total_score >= min_score)
        .order_by(ScannerResult.rank.asc())
        .limit(100)
        .all()
    )
    return _enrich(rows, db)


@router.get('/top-opportunities', response_model=list[ScannerResultOut])
def top_opportunities(limit: int = 20, db: Session = Depends(get_db)) -> list[dict]:
    # Fetch more rows than needed, then deduplicate by ticker
    rows = (
        db.query(ScannerResult)
        .order_by(desc(ScannerResult.priority_score))
        .limit(200)
        .all()
    )
    # Keep highest-scoring entry per ticker
    seen: dict = {}
    for row in rows:
        if row.asset_id not in seen or row.priority_score > seen[row.asset_id].priority_score:
            seen[row.asset_id] = row
    deduped = sorted(seen.values(), key=lambda r: r.priority_score, reverse=True)[:limit]
    return _enrich(deduped, db)


@router.get('/emerging', response_model=list[ScannerResultOut])
def emerging(db: Session = Depends(get_db)) -> list[dict]:
    rows = (
        db.query(ScannerResult)
        .filter(ScannerResult.scanner_type == 'early_growth')
        .order_by(desc(ScannerResult.priority_score))
        .limit(20)
        .all()
    )
    return _enrich(rows, db)


@router.get('/sectors', summary='List all unique sectors in the scanner universe')
def list_sectors(db: Session = Depends(get_db)) -> list[str]:
    """Return sorted list of unique sector values from scanned assets."""
    from sqlalchemy import distinct
    rows = (
        db.query(distinct(Asset.sector))
        .join(ScannerResult, ScannerResult.asset_id == Asset.id)
        .filter(Asset.sector.isnot(None))
        .all()
    )
    return sorted(r[0] for r in rows if r[0])
