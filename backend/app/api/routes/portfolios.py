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


# ─────────────────────────────────────────────────────────────────────────────
# Simplified position endpoints (P6) — auto-fetch current price, compute P&L
# ─────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel
from datetime import date as _date

class AddPositionRequest(BaseModel):
    ticker: str
    quantity: float
    entry_price: float
    entry_date: _date
    notes: str | None = None


@router.post('/{portfolio_id}/positions')
def add_position(
    portfolio_id: str,
    payload: AddPositionRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Add a position with auto-fetched asset data and current price."""
    from app.models.asset import Asset, AssetPriceDaily
    from app.models.portfolio import PositionLot
    from sqlalchemy import desc
    import uuid

    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')

    ticker = payload.ticker.upper().strip()
    asset = db.query(Asset).filter(Asset.ticker == ticker).first()
    if not asset:
        # Create asset and try to populate basic info
        try:
            from app.services.ingestion.providers import get_market_data_provider
            from app.core.config import get_settings
            from app.services.ingestion.logic import ingest_ticker
            provider = get_market_data_provider(get_settings())
            ingest_ticker(db, provider, ticker)
            db.flush()
            asset = db.query(Asset).filter(Asset.ticker == ticker).first()
        except Exception:
            pass

    if not asset:
        # Last resort: create minimal asset
        asset = Asset(
            id=str(uuid.uuid4()),
            ticker=ticker,
            name=ticker,
            exchange='NASDAQ',
            currency='USD',
        )
        db.add(asset)
        db.flush()

    invested = payload.quantity * payload.entry_price

    # Get current price for P&L calculation
    current_price = None
    latest_px = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id == asset.id)
        .order_by(desc(AssetPriceDaily.date))
        .first()
    )
    if latest_px:
        current_price = latest_px.close

    current_value = payload.quantity * current_price if current_price else None

    position = Position(
        id=str(uuid.uuid4()),
        portfolio_id=portfolio.id,
        asset_id=asset.id,
        first_buy_date=payload.entry_date,
        avg_cost=payload.entry_price,
        quantity=payload.quantity,
        invested_amount=invested,
        current_value=current_value,
        thesis=payload.notes,
        status='open',
    )
    db.add(position)
    db.flush()

    lot = PositionLot(
        id=str(uuid.uuid4()),
        position_id=position.id,
        buy_date=payload.entry_date,
        quantity=payload.quantity,
        price=payload.entry_price,
        fees=0,
        notes=payload.notes,
    )
    db.add(lot)
    db.commit()
    db.refresh(position)

    pnl = (current_value - invested) if current_value else 0
    pnl_pct = (pnl / invested * 100) if invested else 0

    return {
        'id': position.id,
        'ticker': asset.ticker,
        'name': asset.name,
        'quantity': position.quantity,
        'entry_price': position.avg_cost,
        'invested': invested,
        'current_price': current_price,
        'current_value': current_value,
        'pnl': round(pnl, 2),
        'pnl_pct': round(pnl_pct, 2),
    }


@router.delete('/{portfolio_id}/positions/{position_id}')
def delete_position(portfolio_id: str, position_id: str, db: Session = Depends(get_db)) -> dict:
    """Delete a position and its lots."""
    from app.models.portfolio import PositionLot
    pos = db.query(Position).filter(Position.id == position_id, Position.portfolio_id == portfolio_id).first()
    if not pos:
        raise HTTPException(status_code=404, detail='Position not found')
    db.query(PositionLot).filter(PositionLot.position_id == position_id).delete()
    db.delete(pos)
    db.commit()
    return {'deleted': position_id}


@router.post('/{portfolio_id}/positions/refresh')
def refresh_positions(portfolio_id: str, db: Session = Depends(get_db)) -> dict:
    """Refresh current_value for all positions using latest prices."""
    from app.models.asset import AssetPriceDaily
    from sqlalchemy import desc

    positions = db.query(Position).filter(Position.portfolio_id == portfolio_id).all()
    updated = 0
    total_invested = 0.0
    total_value = 0.0
    for pos in positions:
        latest = (
            db.query(AssetPriceDaily)
            .filter(AssetPriceDaily.asset_id == pos.asset_id)
            .order_by(desc(AssetPriceDaily.date))
            .first()
        )
        if latest:
            pos.current_value = pos.quantity * latest.close
            updated += 1
        total_invested += pos.invested_amount
        total_value += pos.current_value or pos.invested_amount

    db.commit()
    pnl = total_value - total_invested
    return {
        'updated': updated,
        'total_positions': len(positions),
        'total_invested': round(total_invested, 2),
        'total_value': round(total_value, 2),
        'total_pnl': round(pnl, 2),
        'total_pnl_pct': round(pnl / total_invested * 100, 2) if total_invested else 0,
    }
