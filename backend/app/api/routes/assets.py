from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models.asset import Asset, AssetEvent, AssetPriceDaily, AssetScoreDaily
from app.schemas.asset import AssetEventOut, AssetOut, AssetScoreOut, PricePoint
from app.services.ingestion.logic import ingest_ticker, upsert_asset_from_provider
from app.services.ingestion.providers import get_market_data_provider
from app.services.scoring.engine import refresh_asset_score

router = APIRouter()


@router.get('/search', response_model=list[AssetOut])
def search_assets(q: str = Query(..., min_length=1), db: Session = Depends(get_db)) -> list[Asset]:
    return (
        db.query(Asset)
        .filter((Asset.ticker.ilike(f'%{q}%')) | (Asset.name.ilike(f'%{q}%')))
        .order_by(Asset.ticker.asc())
        .limit(25)
        .all()
    )


@router.get('/{ticker}', response_model=AssetOut)
def get_asset(ticker: str, db: Session = Depends(get_db)) -> Asset:
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    return asset


@router.post('/{ticker}/bootstrap', response_model=AssetOut)
def bootstrap_asset(ticker: str, db: Session = Depends(get_db)) -> Asset:
    settings = get_settings()
    provider = get_market_data_provider(settings)
    asset = upsert_asset_from_provider(db, provider, ticker.upper(), default_exchange=settings.default_exchange)
    db.commit()
    db.refresh(asset)
    return asset


@router.post('/{ticker}/ingest')
def ingest_asset(ticker: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    provider = get_market_data_provider(settings)
    result = ingest_ticker(
        db,
        provider,
        ticker=ticker.upper(),
        default_exchange=settings.default_exchange,
        days=settings.yfinance_history_days,
        source=settings.data_provider,
    )
    db.commit()
    return {'provider': settings.data_provider, **result}


@router.post('/ingest/batch')
def ingest_assets_batch(tickers: list[str], db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    provider = get_market_data_provider(settings)
    results = []
    for ticker in tickers:
        results.append(
            ingest_ticker(
                db,
                provider,
                ticker=ticker.upper(),
                default_exchange=settings.default_exchange,
                days=settings.yfinance_history_days,
                source=settings.data_provider,
            )
        )
    db.commit()
    return {'provider': settings.data_provider, 'count': len(results), 'results': results}


@router.post('/{ticker}/refresh-all')
def refresh_asset_all(ticker: str, as_of: date | None = None, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    provider = get_market_data_provider(settings)
    result = ingest_ticker(
        db,
        provider,
        ticker=ticker.upper(),
        default_exchange=settings.default_exchange,
        days=settings.yfinance_history_days,
        source=settings.data_provider,
    )
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found after ingestion')
    score = refresh_asset_score(db, asset, as_of=as_of)
    db.commit()
    db.refresh(score)
    return {'provider': settings.data_provider, 'ingestion': result, 'score_date': score.date, 'score_total': score.total_score}


@router.post('/{ticker}/recompute', response_model=AssetScoreOut)
def recompute_asset_score(ticker: str, as_of: date | None = None, db: Session = Depends(get_db)) -> AssetScoreDaily:
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    score = refresh_asset_score(db, asset, as_of=as_of)
    db.commit()
    db.refresh(score)
    return score


@router.get('/{ticker}/scores', response_model=list[AssetScoreOut])
def get_asset_scores(ticker: str, db: Session = Depends(get_db)) -> list[AssetScoreDaily]:
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    return db.query(AssetScoreDaily).filter(AssetScoreDaily.asset_id == asset.id).order_by(desc(AssetScoreDaily.date)).limit(90).all()


@router.get('/{ticker}/events', response_model=list[AssetEventOut])
def get_asset_events(ticker: str, db: Session = Depends(get_db)) -> list[AssetEvent]:
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    return db.query(AssetEvent).filter(AssetEvent.asset_id == asset.id).order_by(desc(AssetEvent.event_date)).limit(50).all()


@router.get('/{ticker}/chart', response_model=list[PricePoint])
def get_asset_chart(ticker: str, db: Session = Depends(get_db)) -> list[PricePoint]:
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    rows = db.query(AssetPriceDaily).filter(AssetPriceDaily.asset_id == asset.id).order_by(AssetPriceDaily.date.asc()).limit(365).all()
    return [PricePoint(date=row.date, close=row.close) for row in rows]
