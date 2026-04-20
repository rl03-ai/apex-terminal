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


@router.get('/{portfolio_id}/positions')
def get_portfolio_positions(portfolio_id: str, db: Session = Depends(get_db)) -> list[dict]:
    from app.models.asset import Asset, AssetPriceDaily
    from sqlalchemy import desc

    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')

    positions = db.query(Position).filter(Position.portfolio_id == portfolio.id).all()
    result: list[dict] = []
    for p in positions:
        asset = db.query(Asset).filter(Asset.id == p.asset_id).first()
        latest_px = (
            db.query(AssetPriceDaily)
            .filter(AssetPriceDaily.asset_id == p.asset_id)
            .order_by(desc(AssetPriceDaily.date))
            .first()
        )
        current_value = p.quantity * latest_px.close if latest_px else p.current_value
        result.append({
            'id': p.id,
            'portfolio_id': p.portfolio_id,
            'asset_id': p.asset_id,
            'ticker': asset.ticker if asset else '?',
            'asset_name': asset.name if asset else '?',
            'status': p.status,
            'first_buy_date': p.first_buy_date.isoformat() if p.first_buy_date else None,
            'avg_cost': p.avg_cost,
            'quantity': p.quantity,
            'invested_amount': p.invested_amount,
            'current_value': current_value,
            'thesis': p.thesis,
        })
    return result


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



# ─────────────────────────────────────────────────────────────────────────────
# Transactions — buy/sell history per position
# ─────────────────────────────────────────────────────────────────────────────

class TransactionRequest(BaseModel):
    type: str              # 'buy' or 'sell'
    quantity: float
    price: float
    date: _date
    notes: str | None = None


def _recompute_position(db, position: Position) -> tuple[float, float, float, float]:
    """Recompute position aggregates from all lots (weighted avg cost, realised P&L)."""
    from app.models.portfolio import PositionLot
    lots = (
        db.query(PositionLot)
        .filter(PositionLot.position_id == position.id)
        .order_by(PositionLot.buy_date.asc(), PositionLot.created_at.asc())
        .all()
    )

    avg_cost = 0.0
    quantity = 0.0
    realised_pnl = 0.0

    for lot in lots:
        qty = lot.quantity
        price = lot.price
        if qty > 0:
            # Buy — weighted average
            total_cost_before = avg_cost * quantity
            total_cost_new    = price * qty
            quantity_new      = quantity + qty
            if quantity_new > 0:
                avg_cost = (total_cost_before + total_cost_new) / quantity_new
            quantity = quantity_new
        else:
            # Sell — reduces qty, avg_cost unchanged
            sell_qty = abs(qty)
            realised_pnl += (price - avg_cost) * sell_qty
            quantity -= sell_qty

    invested = avg_cost * quantity if quantity > 0 else 0.0
    return round(avg_cost, 4), round(quantity, 6), round(invested, 2), round(realised_pnl, 2)


@router.get('/{portfolio_id}/positions/{position_id}/transactions')
def list_transactions(portfolio_id: str, position_id: str, db: Session = Depends(get_db)) -> list[dict]:
    """List all transactions (lots) for a position in chronological order."""
    from app.models.portfolio import PositionLot
    pos = db.query(Position).filter(
        Position.id == position_id, Position.portfolio_id == portfolio_id
    ).first()
    if not pos:
        raise HTTPException(status_code=404, detail='Position not found')

    lots = (
        db.query(PositionLot)
        .filter(PositionLot.position_id == position_id)
        .order_by(PositionLot.buy_date.asc())
        .all()
    )
    return [
        {
            'id': l.id,
            'type': 'buy' if l.quantity > 0 else 'sell',
            'date': l.buy_date.isoformat(),
            'quantity': abs(l.quantity),
            'price': l.price,
            'value': abs(l.quantity) * l.price,
            'fees': l.fees,
            'notes': l.notes,
        }
        for l in lots
    ]


@router.post('/{portfolio_id}/positions/{position_id}/transactions')
def add_transaction(
    portfolio_id: str,
    position_id: str,
    payload: TransactionRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Add a buy or sell transaction to an existing position."""
    from app.models.portfolio import PositionLot
    import uuid as _uuid

    pos = db.query(Position).filter(
        Position.id == position_id, Position.portfolio_id == portfolio_id
    ).first()
    if not pos:
        raise HTTPException(status_code=404, detail='Position not found')

    tx_type = payload.type.lower().strip()
    if tx_type not in ('buy', 'sell'):
        raise HTTPException(status_code=400, detail='type must be "buy" or "sell"')
    if payload.quantity <= 0 or payload.price <= 0:
        raise HTTPException(status_code=400, detail='quantity and price must be positive')

    # Store buys as positive qty, sells as negative qty
    signed_qty = payload.quantity if tx_type == 'buy' else -payload.quantity

    # Validate: cannot sell more than held
    if tx_type == 'sell':
        _, current_qty, _, _ = _recompute_position(db, pos)
        if payload.quantity > current_qty + 1e-6:
            raise HTTPException(
                status_code=400,
                detail=f'Insufficient shares: trying to sell {payload.quantity}, only {current_qty:.4f} available'
            )

    lot = PositionLot(
        id=str(_uuid.uuid4()),
        position_id=pos.id,
        buy_date=payload.date,
        quantity=signed_qty,
        price=payload.price,
        fees=0,
        notes=payload.notes or f'{tx_type.upper()}',
    )
    db.add(lot)
    db.flush()

    # Recompute position aggregates
    avg_cost, quantity, invested, realised_pnl = _recompute_position(db, pos)
    pos.avg_cost = avg_cost
    pos.quantity = quantity
    pos.invested_amount = invested
    # If fully sold, mark as closed
    if quantity < 1e-6:
        pos.status = 'closed'
    else:
        pos.status = 'open'

    db.commit()
    return {
        'transaction_id': lot.id,
        'position': {
            'id': pos.id,
            'avg_cost': avg_cost,
            'quantity': quantity,
            'invested': invested,
            'realised_pnl': realised_pnl,
            'status': pos.status,
        }
    }


@router.delete('/{portfolio_id}/positions/{position_id}/transactions/{tx_id}')
def delete_transaction(
    portfolio_id: str,
    position_id: str,
    tx_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Delete a transaction and recompute position."""
    from app.models.portfolio import PositionLot
    pos = db.query(Position).filter(
        Position.id == position_id, Position.portfolio_id == portfolio_id
    ).first()
    if not pos:
        raise HTTPException(status_code=404, detail='Position not found')

    lot = db.query(PositionLot).filter(
        PositionLot.id == tx_id, PositionLot.position_id == position_id
    ).first()
    if not lot:
        raise HTTPException(status_code=404, detail='Transaction not found')

    db.delete(lot)
    db.flush()

    avg_cost, quantity, invested, realised_pnl = _recompute_position(db, pos)
    pos.avg_cost = avg_cost
    pos.quantity = quantity
    pos.invested_amount = invested
    pos.status = 'closed' if quantity < 1e-6 else 'open'
    db.commit()

    return {
        'deleted': tx_id,
        'position': {
            'avg_cost': avg_cost,
            'quantity': quantity,
            'invested': invested,
            'realised_pnl': realised_pnl,
            'status': pos.status,
        }
    }


@router.get('/{portfolio_id}/positions/{position_id}/summary')
def position_summary(portfolio_id: str, position_id: str, db: Session = Depends(get_db)) -> dict:
    """Full position summary: avg_cost, qty, P&L unrealised + realised, current price."""
    from app.models.asset import Asset, AssetPriceDaily
    from sqlalchemy import desc

    pos = db.query(Position).filter(
        Position.id == position_id, Position.portfolio_id == portfolio_id
    ).first()
    if not pos:
        raise HTTPException(status_code=404, detail='Position not found')

    avg_cost, quantity, invested, realised_pnl = _recompute_position(db, pos)
    asset = db.query(Asset).filter(Asset.id == pos.asset_id).first()
    latest_px = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id == pos.asset_id)
        .order_by(desc(AssetPriceDaily.date))
        .first()
    )
    current_price = latest_px.close if latest_px else avg_cost
    current_value = quantity * current_price
    unrealised_pnl = current_value - invested if quantity > 0 else 0
    unrealised_pct = (unrealised_pnl / invested * 100) if invested > 0 else 0

    return {
        'id': pos.id,
        'ticker': asset.ticker if asset else '?',
        'name': asset.name if asset else '?',
        'status': pos.status,
        'avg_cost': avg_cost,
        'quantity': quantity,
        'invested': invested,
        'current_price': current_price,
        'current_value': round(current_value, 2),
        'unrealised_pnl': round(unrealised_pnl, 2),
        'unrealised_pct': round(unrealised_pct, 2),
        'realised_pnl': realised_pnl,
        'total_pnl': round(unrealised_pnl + realised_pnl, 2),
    }
