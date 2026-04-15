from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.asset import AssetPriceDaily, AssetScoreDaily
from app.models.portfolio import Portfolio, Position, PositionScenario, PositionSnapshotDaily
from app.services.forecast.scenarios import build_scenarios


def calculate_position_metrics(quantity: float, avg_cost: float, current_price: float) -> dict[str, float]:
    market_value = quantity * current_price
    invested_amount = quantity * avg_cost
    pnl = market_value - invested_amount
    pnl_pct = (pnl / invested_amount * 100) if invested_amount else 0.0
    return {
        'market_value': round(market_value, 2),
        'pnl': round(pnl, 2),
        'pnl_pct': round(pnl_pct, 2),
    }


def derive_thesis_status(score_now: float, score_at_entry: float | None = None) -> str:
    if score_at_entry is None:
        return 'intact'
    diff = score_now - score_at_entry
    if diff >= 10:
        return 'strengthening'
    if diff >= -5:
        return 'intact'
    if diff >= -10:
        return 'under_review'
    if diff >= -20:
        return 'deteriorating'
    return 'broken'


def refresh_position_snapshot(db: Session, position: Position, portfolio_total: float | None = None) -> PositionSnapshotDaily | None:
    latest_price = (
        db.query(AssetPriceDaily)
        .filter(AssetPriceDaily.asset_id == position.asset_id)
        .order_by(AssetPriceDaily.date.desc())
        .first()
    )
    if not latest_price:
        return None
    latest_score = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == position.asset_id)
        .order_by(AssetScoreDaily.date.desc())
        .first()
    )
    entry_score = (
        db.query(AssetScoreDaily)
        .filter(AssetScoreDaily.asset_id == position.asset_id, AssetScoreDaily.date <= position.first_buy_date)
        .order_by(AssetScoreDaily.date.desc())
        .first()
    )
    metrics = calculate_position_metrics(position.quantity, position.avg_cost, latest_price.close)
    position.current_value = metrics['market_value']
    weight = (metrics['market_value'] / portfolio_total * 100) if portfolio_total else None
    score_total = latest_score.total_score if latest_score else None
    thesis_status = derive_thesis_status(score_total or 50.0, entry_score.total_score if entry_score else None)

    snapshot = (
        db.query(PositionSnapshotDaily)
        .filter(PositionSnapshotDaily.position_id == position.id, PositionSnapshotDaily.date == latest_price.date)
        .first()
    )
    if not snapshot:
        snapshot = PositionSnapshotDaily(
            position_id=position.id,
            date=latest_price.date,
            close_price=latest_price.close,
            market_value=0,
            pnl=0,
            pnl_pct=0,
        )
        db.add(snapshot)
    snapshot.close_price = latest_price.close
    snapshot.market_value = metrics['market_value']
    snapshot.pnl = metrics['pnl']
    snapshot.pnl_pct = metrics['pnl_pct']
    snapshot.weight_in_portfolio = round(weight, 2) if weight is not None else None
    snapshot.score_total = score_total
    snapshot.score_growth = latest_score.growth_score if latest_score else None
    snapshot.score_quality = latest_score.quality_score if latest_score else None
    snapshot.score_narrative = latest_score.narrative_score if latest_score else None
    snapshot.score_market = latest_score.market_score if latest_score else None
    snapshot.score_risk = latest_score.risk_score if latest_score else None
    snapshot.thesis_status = thesis_status
    snapshot.scenario_status = 'base' if (latest_score and latest_score.total_score >= 65) else 'watch'

    if latest_score:
        scenario_values = build_scenarios(
            current_price=latest_price.close,
            total_score=latest_score.total_score,
            growth_score=latest_score.growth_score,
            quality_score=latest_score.quality_score,
            risk_score=latest_score.risk_score,
        )
        scenario = (
            db.query(PositionScenario)
            .filter(PositionScenario.position_id == position.id, PositionScenario.as_of_date == latest_price.date)
            .first()
        )
        if not scenario:
            scenario = PositionScenario(position_id=position.id, as_of_date=latest_price.date, **scenario_values, summary={})
            db.add(scenario)
        else:
            for key, value in scenario_values.items():
                setattr(scenario, key, value)
        scenario.summary = {
            'current_price': latest_price.close,
            'score_total': latest_score.total_score,
            'thesis_status': thesis_status,
        }
    db.flush()
    return snapshot


def refresh_portfolio_snapshots(db: Session, portfolio: Portfolio) -> dict[str, float | int]:
    positions = db.query(Position).filter(Position.portfolio_id == portfolio.id, Position.status == 'open').all()
    total_value = 0.0
    latest_prices: dict[str, float] = {}
    for position in positions:
        latest_price = (
            db.query(AssetPriceDaily)
            .filter(AssetPriceDaily.asset_id == position.asset_id)
            .order_by(AssetPriceDaily.date.desc())
            .first()
        )
        if latest_price:
            latest_prices[position.id] = latest_price.close
            total_value += position.quantity * latest_price.close
    for position in positions:
        refresh_position_snapshot(db, position, portfolio_total=total_value)
    return {'positions_processed': len(positions), 'portfolio_value': round(total_value, 2)}
