"""Early signals API routes.

GET  /early-signals         — list active early signals
POST /early-signals/run     — refresh (run scanner)
"""

from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.asset import Asset, AssetPriceDaily
from app.models.early_signal import EarlySignal

router = APIRouter()


@router.get('', summary='List active early signals')
def list_early_signals(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> list[dict]:
    signals = (
        db.query(EarlySignal)
        .filter(EarlySignal.is_active == True)
        .order_by(desc(EarlySignal.signal_score))
        .limit(limit)
        .all()
    )

    result: list[dict] = []
    for s in signals:
        asset = db.query(Asset).filter(Asset.id == s.asset_id).first()
        if not asset:
            continue
        criteria = [c.strip() for c in (s.criteria_passed or '').split(',') if c.strip()]
        result.append({
            'id': s.id,
            'ticker': asset.ticker,
            'name': asset.name,
            'sector': asset.sector,
            'first_detected_date': s.first_detected_date.isoformat() if s.first_detected_date else None,
            'first_detected_price': s.first_detected_price,
            'current_price': s.current_price,
            'pct_move_since': s.pct_move_since,
            'signal_score': s.signal_score,
            'total_score': s.total_score,
            'criteria_passed': criteria,
            'days_active': (
                (s.last_signal_date - s.first_detected_date).days + 1
                if s.last_signal_date and s.first_detected_date else 0
            ),
        })
    return result


@router.post('/run', summary='Refresh early signal scanner')
def run_early_signals(db: Session = Depends(get_db)) -> dict:
    from app.services.scanner.early_signal import refresh_early_signals
    return refresh_early_signals(db)


@router.get('/history/{ticker}', summary='History of early signals for a ticker')
def ticker_history(ticker: str, db: Session = Depends(get_db)) -> list[dict]:
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        return []
    signals = (
        db.query(EarlySignal)
        .filter(EarlySignal.asset_id == asset.id)
        .order_by(desc(EarlySignal.first_detected_date))
        .all()
    )
    return [
        {
            'first_detected_date': s.first_detected_date.isoformat(),
            'first_detected_price': s.first_detected_price,
            'last_signal_date': s.last_signal_date.isoformat() if s.last_signal_date else None,
            'current_price': s.current_price,
            'pct_move_since': s.pct_move_since,
            'signal_score': s.signal_score,
            'is_active': s.is_active,
            'exit_reason': s.exit_reason,
            'criteria_passed': [c.strip() for c in (s.criteria_passed or '').split(',') if c.strip()],
        }
        for s in signals
    ]
