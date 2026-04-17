from __future__ import annotations

from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetEvent, AssetFundamentalsQuarterly, AssetPriceDaily, AssetTechnicalSnapshot
from app.services.ingestion.providers import MarketDataProvider


def upsert_asset_from_provider(db: Session, provider: MarketDataProvider, ticker: str, default_exchange: str = 'NASDAQ') -> Asset:
    profile = provider.fetch_asset_profile(ticker)
    asset = db.execute(select(Asset).where(Asset.ticker == ticker.upper())).scalar_one_or_none()
    if not asset:
        asset = Asset(ticker=ticker.upper(), name=profile.get('name') or ticker.upper())
        db.add(asset)
        db.flush()
    asset.name = profile.get('name') or asset.name
    asset.exchange = profile.get('exchange') or default_exchange
    asset.sector = profile.get('sector')
    asset.industry = profile.get('industry')
    asset.country = profile.get('country')
    asset.currency = profile.get('currency') or 'USD'
    asset.market_cap = profile.get('market_cap')
    return asset


def refresh_asset_prices_and_technicals(db: Session, provider: MarketDataProvider, asset: Asset, days: int = 370, source: str = 'provider') -> int:
    rows = provider.fetch_price_history(asset.ticker, days=days)
    db.query(AssetPriceDaily).filter(AssetPriceDaily.asset_id == asset.id).delete()
    inserted = 0
    for row in rows:
        db.add(AssetPriceDaily(asset_id=asset.id, source=source, **row))
        inserted += 1
    if rows:
        technical = _build_technical_snapshot(asset.id, rows)
        existing = db.query(AssetTechnicalSnapshot).filter(AssetTechnicalSnapshot.asset_id == asset.id, AssetTechnicalSnapshot.date == technical['date']).first()
        if not existing:
            existing = AssetTechnicalSnapshot(asset_id=asset.id, date=technical['date'])
            db.add(existing)
        for key, value in technical.items():
            setattr(existing, key, value)
    return inserted


def refresh_asset_fundamentals(db: Session, provider: MarketDataProvider, asset: Asset, source: str = 'provider') -> int:
    rows = provider.fetch_quarterly_fundamentals(asset.ticker)
    db.query(AssetFundamentalsQuarterly).filter(AssetFundamentalsQuarterly.asset_id == asset.id).delete()
    inserted = 0
    for row in rows:
        db.add(AssetFundamentalsQuarterly(asset_id=asset.id, source=source, **row))
        inserted += 1
    return inserted


def refresh_asset_events(db: Session, provider: MarketDataProvider, asset: Asset) -> int:
    rows = provider.fetch_events(asset.ticker)
    db.query(AssetEvent).filter(AssetEvent.asset_id == asset.id).delete()
    inserted = 0
    for row in rows:
        db.add(AssetEvent(asset_id=asset.id, **row))
        inserted += 1
    return inserted


def ingest_ticker(db: Session, provider: MarketDataProvider, ticker: str, default_exchange: str = 'NASDAQ', days: int = 370, source: str = 'provider') -> dict:
    asset = upsert_asset_from_provider(db, provider, ticker=ticker, default_exchange=default_exchange)
    prices = refresh_asset_prices_and_technicals(db, provider, asset, days=days, source=source)
    fundamentals = refresh_asset_fundamentals(db, provider, asset, source=source)

    # Use full catalyst engine (earnings + insider + news) when available
    events = 0
    try:
        from app.services.catalyst.aggregator import fetch_and_compute_catalyst
        catalyst_events, catalyst_score = fetch_and_compute_catalyst(
            ticker,
            sector=asset.sector,
            industry=asset.industry,
            market_cap=asset.market_cap,
        )
        db.query(AssetEvent).filter(AssetEvent.asset_id == asset.id).delete()
        for row in catalyst_events:
            db.add(AssetEvent(asset_id=asset.id, **row))
        events = len(catalyst_events)
        # Store catalyst score in a JSON field on asset or log it
        import logging as _log
        _log.getLogger(__name__).debug(
            "Catalyst %s: score=%.1f type=%s", ticker, catalyst_score.score, catalyst_score.catalyst_type
        )
    except Exception as exc:
        # Fallback to provider events if catalyst engine fails
        import logging as _log
        _log.getLogger(__name__).warning("Catalyst engine failed for %s (%s), falling back to provider events", ticker, exc)
        events = refresh_asset_events(db, provider, asset)

    db.flush()
    return {
        'ticker': asset.ticker,
        'asset_id': asset.id,
        'prices': prices,
        'fundamentals': fundamentals,
        'events': events,
    }


def _build_technical_snapshot(asset_id: str, rows: list[dict]) -> dict:
    closes = [float(r['close']) for r in rows]
    volumes = [int(r['volume']) for r in rows]
    close_now = closes[-1]
    ma50 = _rolling_mean(closes, 50)
    ma200 = _rolling_mean(closes, 200)
    rs_3m = _pct_change(close_now, closes[-64] if len(closes) > 64 else closes[0])
    rs_6m = _pct_change(close_now, closes[-127] if len(closes) > 127 else closes[0])
    max_52w = max(closes[-252:] if len(closes) >= 252 else closes)
    distance_to_52w_high = round(((max_52w - close_now) / max_52w) * 100, 2) if max_52w else None
    trend_state = 'uptrend' if close_now > ma200 and ma50 >= ma200 else 'mixed'
    if close_now < ma200 and ma50 < ma200:
        trend_state = 'downtrend'
    return {
        'date': rows[-1]['date'],
        'ma50': ma50,
        'ma200': ma200,
        'rsi14': _rsi(closes, 14),
        'relative_strength_3m': _normalize_relative_strength(rs_3m),
        'relative_strength_6m': _normalize_relative_strength(rs_6m),
        'distance_to_52w_high': distance_to_52w_high,
        'volume_avg_20d': round(mean(volumes[-20:]), 2) if len(volumes) >= 20 else round(mean(volumes), 2),
        'trend_state': trend_state,
    }


def _rolling_mean(values: list[float], window: int) -> float:
    sample = values[-window:] if len(values) >= window else values
    return round(mean(sample), 2)


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / abs(previous)) * 100


def _normalize_relative_strength(change_pct: float) -> float:
    return round(max(0.0, min(100.0, 50 + change_pct)), 2)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0.0, d) for d in deltas[-period:]]
    losses = [abs(min(0.0, d)) for d in deltas[-period:]]
    avg_gain = mean(gains) if gains else 0.0
    avg_loss = mean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)
