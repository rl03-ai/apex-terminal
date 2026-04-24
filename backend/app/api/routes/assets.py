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


@router.get('/search')
def search_assets(q: str = Query(..., min_length=1), db: Session = Depends(get_db)) -> list[dict]:
    """Search assets by ticker or name, returning latest price for each."""
    q_upper = q.upper()
    # Prioritize exact ticker match, then prefix match, then substring match
    assets = (
        db.query(Asset)
        .filter((Asset.ticker.ilike(f'%{q}%')) | (Asset.name.ilike(f'%{q}%')))
        .order_by(Asset.ticker.asc())
        .limit(20)
        .all()
    )

    # Sort so exact matches come first, then prefix matches
    def sort_key(a):
        if a.ticker == q_upper: return 0
        if a.ticker.startswith(q_upper): return 1
        return 2
    assets.sort(key=sort_key)

    result: list[dict] = []
    for a in assets:
        latest_px = (
            db.query(AssetPriceDaily)
            .filter(AssetPriceDaily.asset_id == a.id)
            .order_by(desc(AssetPriceDaily.date))
            .first()
        )
        result.append({
            'ticker': a.ticker,
            'name': a.name,
            'sector': a.sector,
            'current_price': latest_px.close if latest_px else None,
        })
    return result


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


@router.get('/{ticker}/score')
def get_asset_score_detail(ticker: str, db: Session = Depends(get_db)) -> dict:
    """Combined endpoint: asset profile + latest score with full explanation."""
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    score = db.query(AssetScoreDaily).filter(
        AssetScoreDaily.asset_id == asset.id
    ).order_by(desc(AssetScoreDaily.date)).first()
    return {
        'ticker': asset.ticker,
        'name': asset.name,
        'sector': asset.sector,
        'industry': asset.industry,
        'market_cap': asset.market_cap,
        'score': {
            'total_score': score.total_score if score else None,
            'growth_score': score.growth_score if score else None,
            'quality_score': score.quality_score if score else None,
            'valuation_score': score.valuation_score if score else None,
            'market_score': score.market_score if score else None,
            'narrative_score': score.narrative_score if score else None,
            'risk_score': score.risk_score if score else None,
            'state': score.state if score else None,
            'score_percentile': score.score_percentile if score else None,
            'score_regime': score.score_regime if score else None,
            'score_trajectory': score.score_trajectory if score else None,
            'explanation': score.explanation if score else {},
        } if score else None,
    }


@router.get('/{ticker}/prices')
def get_asset_prices(ticker: str, db: Session = Depends(get_db)) -> dict:
    """Price history for charting."""
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')
    rows = db.query(AssetPriceDaily).filter(
        AssetPriceDaily.asset_id == asset.id
    ).order_by(AssetPriceDaily.date.asc()).limit(365).all()
    return {
        'ticker': asset.ticker,
        'prices': [{'date': str(r.date), 'close': r.close} for r in rows]
    }


@router.get('/{ticker}/trend', summary='Equity trend regime + entry timing')
def get_asset_trend(ticker: str, db: Session = Depends(get_db)) -> dict:
    """Run TrendChange detector on this asset's price history."""
    from app.services.technical.equity_trend import analyse_from_db
    asset = db.query(Asset).filter(Asset.ticker == ticker.upper()).first()
    if not asset:
        raise HTTPException(status_code=404, detail='Asset not found')

    trend = analyse_from_db(asset.id, db)
    return {
        'ticker': asset.ticker,
        'regime': trend.regime,
        'entry_signal': trend.entry_signal,
        'score_daily': trend.score_daily,
        'score_weekly': trend.score_weekly,
        'confidence': trend.confidence,
        'reasons': trend.reasons,
        'market_score_boost': trend.market_score_boost,
    }



# ─────────────────────────────────────────────────────────────────────────────
# Position transactions (buy/sell lots)
# ─────────────────────────────────────────────────────────────────────────────

from pydantic import BaseModel
from datetime import date as _date
from app.models.portfolio import Position, PositionLot
import uuid

class TransactionRequest(BaseModel):
    type: str              # 'buy' or 'sell'
    quantity: float
    price: float
    date: _date
    notes: str | None = None


def _recompute_position_from_lots(db, position_id: str) -> tuple[float, float, float, float]:
    """
    Recompute avg_cost, quantity, invested_amount, realised_pnl from lots.

    Weighted-average cost for buys.
    Sells keep the avg_cost unchanged (standard accounting).

    Returns (avg_cost, quantity, invested_amount, realised_pnl).
    """
    lots = (
        db.query(PositionLot)
        .filter(PositionLot.position_id == position_id)
        .order_by(PositionLot.buy_date.asc(), PositionLot.created_at.asc())
        .all()
    )

    avg_cost = 0.0
    quantity = 0.0
    realised_pnl = 0.0

    for lot in lots:
        qty = lot.quantity
        price = lot.price
        # Lots with positive quantity are buys, negative are sells (convention)
        if qty > 0:
            # Weighted average cost update
            total_cost_before = avg_cost * quantity
            total_cost_new = price * qty
            quantity_new = quantity + qty
            if quantity_new > 0:
                avg_cost = (total_cost_before + total_cost_new) / quantity_new
            quantity = quantity_new
        else:
            # Sell — reduces quantity, avg_cost unchanged, realised P&L updates
            sell_qty = abs(qty)
            realised_pnl += (price - avg_cost) * sell_qty
            quantity -= sell_qty

    # Invested amount = avg_cost * current qty (cost basis of open position)
    invested = avg_cost * quantity if quantity > 0 else 0.0
    return avg_cost, quantity, invested, realised_pnl


@router.get('/{ticker}/_placeholder', include_in_schema=False)
def _ticker_placeholder_stub(ticker: str) -> dict:
    # Prevents collision with position routes below
    return {}


# ── Real-time quote cache (5 min TTL) ────────────────────────────────────────
import time as _time
_quote_cache: dict[str, tuple[float, dict]] = {}  # ticker -> (expires_at, data)
_QUOTE_TTL = 300  # 5 minutes


@router.get('/{ticker}/quote', summary='Real-time quote via Finnhub')
def get_realtime_quote(ticker: str, db: Session = Depends(get_db)) -> dict:
    """Fetch real-time price from Finnhub with 5-minute cache. Falls back to DB price."""
    ticker = ticker.upper().strip()

    # Check cache
    if ticker in _quote_cache:
        expires_at, cached = _quote_cache[ticker]
        if _time.time() < expires_at:
            return cached

    # Try Finnhub
    result: dict | None = None
    try:
        import httpx
        api_key = os.getenv('FINNHUB_API_KEY', '')
        if api_key:
            resp = httpx.get(
                f'https://finnhub.io/api/v1/quote',
                params={'symbol': ticker, 'token': api_key},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('c') and data['c'] > 0:
                    result = {
                        'ticker': ticker,
                        'price': data['c'],
                        'high': data.get('h'),
                        'low': data.get('l'),
                        'open': data.get('o'),
                        'prev_close': data.get('pc'),
                        'change': round(data['c'] - data.get('pc', data['c']), 2),
                        'change_pct': round((data['c'] - data.get('pc', data['c'])) / data.get('pc', data['c']) * 100, 2) if data.get('pc') else 0,
                        'source': 'finnhub',
                        'timestamp': data.get('t'),
                    }
    except Exception as e:
        logger.debug("Finnhub quote failed for %s: %s", ticker, e)

    # Fallback to DB
    if not result:
        from sqlalchemy import desc as _desc
        asset = db.query(Asset).filter(Asset.ticker == ticker).first()
        if asset:
            latest = (
                db.query(AssetPriceDaily)
                .filter(AssetPriceDaily.asset_id == asset.id)
                .order_by(_desc(AssetPriceDaily.date))
                .first()
            )
            if latest:
                result = {
                    'ticker': ticker,
                    'price': latest.close,
                    'high': latest.high,
                    'low': latest.low,
                    'open': latest.open,
                    'prev_close': None,
                    'change': None,
                    'change_pct': None,
                    'source': 'db',
                    'timestamp': None,
                }

    if not result:
        raise HTTPException(status_code=404, detail=f'No price data for {ticker}')

    # Cache result
    _quote_cache[ticker] = (_time.time() + _QUOTE_TTL, result)
    return result
