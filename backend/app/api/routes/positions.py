from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.asset import Asset, AssetPriceDaily, AssetScoreDaily
from app.models.portfolio import Portfolio, Position, PositionLot, PositionNote, PositionScenario, PositionSnapshotDaily
from app.models.user import User
from app.schemas.portfolio import (
    PositionCreate,
    PositionLotCreate,
    PositionLotOut,
    PositionNoteCreate,
    PositionNoteOut,
    PositionOut,
    PositionScenarioOut,
    PositionSnapshotOut,
)
from app.services.forecast.scenarios import build_scenarios
from app.services.portfolio.logic import refresh_position_snapshot

router = APIRouter()


def _get_user_position(db: Session, position_id: str, user_id: str) -> Position:
    position = (
        db.query(Position)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .filter(Position.id == position_id, Portfolio.user_id == user_id)
        .first()
    )
    if not position:
        raise HTTPException(status_code=404, detail='Position not found')
    return position


@router.post('/portfolio/{portfolio_id}', response_model=PositionOut)
def create_position(
    portfolio_id: str,
    payload: PositionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Position:
    portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id, Portfolio.user_id == current_user.id).first()
    if not portfolio:
        raise HTTPException(status_code=404, detail='Portfolio not found')

    asset = db.query(Asset).filter(Asset.ticker == payload.ticker.upper()).first()
    if not asset:
        asset = Asset(ticker=payload.ticker.upper(), name=payload.ticker.upper(), exchange='NASDAQ', currency='USD')
        db.add(asset)
        db.flush()

    position = Position(
        portfolio_id=portfolio.id,
        asset_id=asset.id,
        first_buy_date=payload.first_buy_date,
        avg_cost=payload.avg_cost,
        quantity=payload.quantity,
        invested_amount=payload.invested_amount,
        position_type=payload.position_type,
        horizon=payload.horizon,
        thesis=payload.thesis,
        invalidation_rules=payload.invalidation_rules,
        target_weight=payload.target_weight,
        max_weight=payload.max_weight,
    )
    db.add(position)
    db.flush()

    lot = PositionLot(
        position_id=position.id,
        buy_date=payload.first_buy_date,
        quantity=payload.quantity,
        price=payload.avg_cost,
        fees=0,
        notes='Initial lot',
    )
    db.add(lot)
    db.commit()
    db.refresh(position)
    return position


@router.post('/{position_id}/lots', response_model=PositionLotOut)
def add_position_lot(
    position_id: str,
    payload: PositionLotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PositionLot:
    position = _get_user_position(db, position_id, current_user.id)
    lot = PositionLot(position_id=position.id, **payload.model_dump())
    db.add(lot)

    new_quantity = position.quantity + payload.quantity
    new_invested = position.invested_amount + (payload.quantity * payload.price) + payload.fees
    position.avg_cost = round(new_invested / new_quantity, 4)
    position.quantity = new_quantity
    position.invested_amount = new_invested
    db.commit()
    db.refresh(lot)
    return lot


@router.post('/{position_id}/refresh')
def refresh_position(position_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict:
    position = _get_user_position(db, position_id, current_user.id)
    snapshot = refresh_position_snapshot(db, position)
    db.commit()
    return {'position_id': position.id, 'snapshot_created': bool(snapshot)}


@router.get('/{position_id}', response_model=PositionOut)
def get_position(position_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> Position:
    return _get_user_position(db, position_id, current_user.id)


@router.get('/{position_id}/history', response_model=list[PositionSnapshotOut])
def get_position_history(position_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[PositionSnapshotDaily]:
    position = _get_user_position(db, position_id, current_user.id)
    return db.query(PositionSnapshotDaily).filter(PositionSnapshotDaily.position_id == position.id).order_by(desc(PositionSnapshotDaily.date)).limit(365).all()


@router.get('/{position_id}/scenarios', response_model=PositionScenarioOut)
def get_position_scenarios(position_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> PositionScenarioOut:
    position = _get_user_position(db, position_id, current_user.id)
    latest = db.query(PositionScenario).filter(PositionScenario.position_id == position.id).order_by(desc(PositionScenario.as_of_date)).first()
    if latest:
        return latest

    latest_price = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id == position.asset_id)
        .order_by(desc(AssetPriceDaily.date))
        .first()
    )
    latest_score = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == position.asset_id)
        .order_by(desc(AssetScoreDaily.date))
        .first()
    )
    current_price = latest_price.close if latest_price else position.avg_cost
    scenario = build_scenarios(
        current_price=current_price,
        total_score=latest_score.total_score if latest_score else 50.0,
        growth_score=latest_score.growth_score if latest_score else 50.0,
        quality_score=latest_score.quality_score if latest_score else 50.0,
        risk_score=latest_score.risk_score if latest_score else 50.0,
    )
    return PositionScenarioOut(as_of_date=date.today(), summary={'generated': 'ephemeral fallback'}, **scenario)


@router.post('/{position_id}/notes', response_model=PositionNoteOut)
def add_position_note(
    position_id: str,
    payload: PositionNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PositionNote:
    position = _get_user_position(db, position_id, current_user.id)
    note = PositionNote(position_id=position.id, note_type=payload.note_type, content=payload.content)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get('/{position_id}/notes', response_model=list[PositionNoteOut])
def list_position_notes(position_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[PositionNote]:
    position = _get_user_position(db, position_id, current_user.id)
    return db.query(PositionNote).filter(PositionNote.position_id == position.id).order_by(desc(PositionNote.note_date)).all()
