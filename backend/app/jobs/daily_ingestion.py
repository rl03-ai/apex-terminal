from __future__ import annotations

from datetime import date

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.ingestion.logic import ingest_ticker
from app.services.ingestion.providers import get_market_data_provider


def run(tickers: list[str] | None = None) -> dict[str, int | date | str]:
    settings = get_settings()
    provider = get_market_data_provider(settings)
    db = SessionLocal()
    tickers = [t.upper() for t in (tickers or settings.demo_universe)]
    try:
        processed = 0
        for ticker in tickers:
            ingest_ticker(
                db,
                provider,
                ticker=ticker,
                default_exchange=settings.default_exchange,
                days=settings.yfinance_history_days,
                source=settings.data_provider,
            )
            processed += 1
        db.commit()
        return {'assets_refreshed': processed, 'as_of': date.today(), 'provider': settings.data_provider}
    finally:
        db.close()
