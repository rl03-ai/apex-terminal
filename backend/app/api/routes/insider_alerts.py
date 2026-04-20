"""Insider alerts API routes.

GET  /insider-alerts       — list current insider alerts, sorted by dollar amount
POST /insider-alerts/run   — refresh scanner manually
"""

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.asset import Asset
from app.models.insider_alert import InsiderAlertCache

router = APIRouter()


@router.get('', summary='List current insider alerts')
def list_insider_alerts(
    limit: int = 20,
    signal_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(InsiderAlertCache)
    if signal_type:
        query = query.filter(InsiderAlertCache.signal_type == signal_type.upper())
    alerts = query.order_by(desc(InsiderAlertCache.dollar_amount)).limit(limit).all()

    result: list[dict] = []
    for a in alerts:
        asset = db.query(Asset).filter(Asset.id == a.asset_id).first()
        if not asset:
            continue
        result.append({
            'id': a.id,
            'ticker': asset.ticker,
            'name': asset.name,
            'sector': asset.sector,
            'signal_type': a.signal_type,
            'dollar_amount': a.dollar_amount,
            'num_insiders': a.num_insiders,
            'num_transactions': a.num_transactions,
            'largest_single': a.largest_single,
            'most_recent_date': a.most_recent_date.isoformat() if a.most_recent_date else None,
            'total_score': a.total_score,
            'details': a.details.split('; ') if a.details else [],
        })
    return result


@router.post('/run', summary='Refresh insider alerts scanner')
def run_insider_alerts(db: Session = Depends(get_db)) -> dict:
    from app.services.scanner.insider_alert import refresh_insider_alerts
    return refresh_insider_alerts(db)
