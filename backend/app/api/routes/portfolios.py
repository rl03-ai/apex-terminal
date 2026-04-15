from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.portfolio import Portfolio, Position, PositionSnapshotDaily
from app.models.user import User
from app.schemas.portfolio import PortfolioCreate, PortfolioOut, PositionOut
from app.services.portfolio.logic import refresh_portfolio_snapshots

router = APIRouter()


@router.get('', response_model=list[PortfolioOut])
def list_portfolios(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[Portfolio]:
    return db.query(Portfolio).filter(Portfolio.user_id == current_user.id).all()


@router.post('', response_model=PortfolioOut)
def create_portfolio(payload: PortfolioCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> Portfolio:
    portfolio = Portfolio(user_id=current_user.id, name=payload.name, base_currency=payload.base_currency)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.get('/{portfolio_id}', response_model=PortfolioOut)
def get_portfolio(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> Portfolio:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')
    return portfolio


@router.post('/{portfolio_id}/refresh')
def refresh_portfolio(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')
    summary = refresh_portfolio_snapshots(db, portfolio)
    db.commit()
    return summary


@router.get('/{portfolio_id}/positions', response_model=list[PositionOut])
def get_portfolio_positions(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[Position]:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')
    return db.query(Position).filter(Position.portfolio_id == portfolio.id).all()


@router.get('/{portfolio_id}/summary')
def get_portfolio_summary(portfolio_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')
    positions = db.query(Position).filter(Position.portfolio_id == portfolio.id, Position.status == 'open').all()
    latest_snapshots = []
    total_value = 0.0
    total_pnl = 0.0
    for position in positions:
        snapshot = (
            db.query(PositionSnapshotDaily)
            .filter(PositionSnapshotDaily.position_id == position.id)
            .order_by(PositionSnapshotDaily.date.desc())
            .first()
        )
        if snapshot:
            latest_snapshots.append(snapshot)
            total_value += snapshot.market_value
            total_pnl += snapshot.pnl
    return {
        'portfolio_id': portfolio.id,
        'positions': len(positions),
        'market_value': round(total_value, 2),
        'pnl': round(total_pnl, 2),
        'open_alert_candidates': sum(1 for s in latest_snapshots if s.thesis_status in {'under_review', 'deteriorating', 'broken'}),
    }
