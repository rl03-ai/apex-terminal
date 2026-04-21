"""Decision matrix + watchlist endpoints.

GET  /decision-matrix              — full matrix
GET  /decision-matrix?only_watchlist=true
GET  /decision-matrix?exclude_held=false

GET  /watchlist                    — list user's watchlist
POST /watchlist/{ticker}           — add ticker
DELETE /watchlist/{ticker}         — remove ticker
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.asset import Asset
from app.models.user import User
from app.models.watchlist import Watchlist

router = APIRouter()


def _get_demo_user_id(db: Session) -> str:
    """Return demo user ID (no auth in this iteration)."""
    user = db.query(User).filter(User.email == 'demo@apex-terminal.io').first()
    if not user:
        # Fallback: first user
        user = db.query(User).first()
    if not user:
        raise HTTPException(status_code=500, detail='No user found in database')
    return user.id


# ─── Decision matrix ─────────────────────────────────────────────────────────

@router.get('/decision-matrix', summary='Composite decision matrix for entry')
def get_decision_matrix(
    only_watchlist: bool = Query(False, description='Show only watchlisted tickers'),
    exclude_held:   bool = Query(True,  description='Exclude tickers already in portfolio'),
    limit:          int  = Query(80, ge=1, le=500),
    min_verdict:    str  = Query('all', description='Filter min verdict: all, WAIT, GOOD, STRONG_SETUP'),
    db: Session = Depends(get_db),
) -> dict:
    from app.services.decision.matrix import compute_decision_matrix
    user_id = _get_demo_user_id(db)
    all_rows = compute_decision_matrix(
        db, user_id=user_id,
        exclude_held=exclude_held,
        only_watchlist=only_watchlist,
    )

    # Summary counts (full)
    counts = {'STRONG_SETUP': 0, 'GOOD': 0, 'WAIT': 0, 'AVOID': 0}
    for r in all_rows:
        counts[r['verdict']] = counts.get(r['verdict'], 0) + 1

    # Apply min verdict filter
    if min_verdict != 'all':
        order = {'AVOID': 0, 'WAIT': 1, 'GOOD': 2, 'STRONG_SETUP': 3}
        min_rank = order.get(min_verdict, 0)
        rows = [r for r in all_rows if order.get(r['verdict'], 0) >= min_rank]
    else:
        rows = all_rows

    # Apply limit
    rows = rows[:limit]

    return {
        'total_count': len(all_rows),
        'count': len(rows),
        'verdict_counts': counts,
        'matrix': rows,
    }


# ─── Watchlist ───────────────────────────────────────────────────────────────

@router.get('/watchlist', summary='List user watchlist')
def list_watchlist(db: Session = Depends(get_db)) -> list[dict]:
    user_id = _get_demo_user_id(db)
    items = db.query(Watchlist).filter(Watchlist.user_id == user_id).all()
    out: list[dict] = []
    for w in items:
        a = db.query(Asset).filter(Asset.id == w.asset_id).first()
        if not a:
            continue
        out.append({
            'ticker': a.ticker,
            'name': a.name,
            'sector': a.sector,
            'added_at': w.added_at.isoformat() if w.added_at else None,
            'notes': w.notes,
        })
    return out


@router.post('/watchlist/{ticker}', summary='Add ticker to watchlist')
def add_to_watchlist(ticker: str, db: Session = Depends(get_db)) -> dict:
    user_id = _get_demo_user_id(db)
    ticker = ticker.upper().strip()
    asset = db.query(Asset).filter(Asset.ticker == ticker).first()
    if not asset:
        raise HTTPException(status_code=404, detail=f'Asset {ticker} not found')
    existing = db.query(Watchlist).filter(
        Watchlist.user_id == user_id, Watchlist.asset_id == asset.id,
    ).first()
    if existing:
        return {'status': 'already_in_watchlist', 'ticker': ticker}
    import uuid
    db.add(Watchlist(
        id=str(uuid.uuid4()),
        user_id=user_id,
        asset_id=asset.id,
    ))
    db.commit()
    return {'status': 'added', 'ticker': ticker}


@router.delete('/watchlist/{ticker}', summary='Remove ticker from watchlist')
def remove_from_watchlist(ticker: str, db: Session = Depends(get_db)) -> dict:
    user_id = _get_demo_user_id(db)
    ticker = ticker.upper().strip()
    asset = db.query(Asset).filter(Asset.ticker == ticker).first()
    if not asset:
        raise HTTPException(status_code=404, detail=f'Asset {ticker} not found')
    deleted = db.query(Watchlist).filter(
        Watchlist.user_id == user_id, Watchlist.asset_id == asset.id,
    ).delete()
    db.commit()
    return {'status': 'removed' if deleted else 'not_in_watchlist', 'ticker': ticker}
