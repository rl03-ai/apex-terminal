"""Bulk ingestion engine.

Ingests a large universe of tickers in parallel with:
- Configurable worker pool (default 8 threads)
- Per-ticker retry with exponential back-off
- Rate limiting to avoid yfinance 429s
- Progress tracking with structured logging
- Incremental commit every N tickers (avoids one huge transaction)

Usage:
    from app.services.ingestion.bulk import run_bulk_ingest
    result = run_bulk_ingest(tickers=["NVDA", "MSFT", ...], workers=8)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.ingestion.logic import ingest_ticker
from app.services.ingestion.providers import get_market_data_provider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TickerResult:
    ticker: str
    ok: bool
    prices: int = 0
    fundamentals: int = 0
    events: int = 0
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class BulkIngestResult:
    total: int
    succeeded: int
    failed: int
    skipped: int
    as_of: date = field(default_factory=date.today)
    provider: str = "yfinance"
    duration_s: float = 0.0
    errors: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Single-ticker worker (runs inside a thread)
# ---------------------------------------------------------------------------

def _ingest_one(
    ticker: str,
    *,
    settings,
    max_retries: int = 3,
    base_delay: float = 2.0,
) -> TickerResult:
    """Ingest one ticker with retry logic. Each call opens its own DB session."""
    t0 = time.monotonic()
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        db: Session | None = None
        try:
            provider = get_market_data_provider(settings)
            db = SessionLocal()
            result = ingest_ticker(
                db,
                provider,
                ticker=ticker,
                default_exchange=settings.default_exchange,
                days=settings.yfinance_history_days,
                source=settings.data_provider,
            )
            db.commit()
            return TickerResult(
                ticker=ticker,
                ok=True,
                prices=result.get("prices", 0),
                fundamentals=result.get("fundamentals", 0),
                events=result.get("events", 0),
                duration_s=round(time.monotonic() - t0, 2),
            )
        except Exception as exc:
            last_error = str(exc)
            if db:
                try:
                    db.rollback()
                except Exception:
                    pass
            if attempt < max_retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.debug("Retry %d/%d for %s in %.1fs — %s", attempt, max_retries, ticker, delay, exc)
                time.sleep(delay)
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    return TickerResult(
        ticker=ticker,
        ok=False,
        error=last_error,
        duration_s=round(time.monotonic() - t0, 2),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_bulk_ingest(
    tickers: list[str] | None = None,
    *,
    workers: int = 8,
    max_retries: int = 3,
    inter_ticker_delay: float = 0.25,   # seconds between submissions (rate limiting)
    log_every: int = 25,                # log progress every N completions
) -> BulkIngestResult:
    """
    Ingest a list of tickers in parallel.

    Parameters
    ----------
    tickers             : list of uppercase tickers; if None, uses DEMO_UNIVERSE from settings
    workers             : ThreadPoolExecutor max_workers
    max_retries         : per-ticker retry attempts
    inter_ticker_delay  : seconds to sleep between submitting futures (throttle)
    log_every           : log a progress line every N completed tickers
    """
    settings = get_settings()

    if tickers is None:
        from app.services.universe.builder import build_universe
        tickers = build_universe()
        logger.info("No tickers supplied — using auto-built universe (%d tickers)", len(tickers))

    tickers = [t.upper().strip() for t in tickers if t and t.strip()]
    total = len(tickers)
    logger.info("Bulk ingest START — %d tickers, %d workers, provider=%s", total, workers, settings.data_provider)
    wall_start = time.monotonic()

    succeeded: list[TickerResult] = []
    failed: list[TickerResult] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apex-ingest") as pool:
        future_to_ticker: dict = {}
        for ticker in tickers:
            fut = pool.submit(
                _ingest_one,
                ticker,
                settings=settings,
                max_retries=max_retries,
            )
            future_to_ticker[fut] = ticker
            time.sleep(inter_ticker_delay)  # gentle throttle on submission

        for fut in as_completed(future_to_ticker):
            result: TickerResult = fut.result()
            completed += 1
            if result.ok:
                succeeded.append(result)
            else:
                failed.append(result)
                logger.warning("FAILED %s — %s", result.ticker, result.error)

            if completed % log_every == 0 or completed == total:
                pct = completed / total * 100
                elapsed = time.monotonic() - wall_start
                rate = completed / elapsed if elapsed > 0 else 0
                eta_s = (total - completed) / rate if rate > 0 else 0
                logger.info(
                    "Progress %d/%d (%.0f%%) | ok=%d fail=%d | %.1f t/s | ETA %.0fs",
                    completed, total, pct,
                    len(succeeded), len(failed),
                    rate, eta_s,
                )

    wall_elapsed = round(time.monotonic() - wall_start, 1)
    logger.info(
        "Bulk ingest DONE — %d ok / %d failed / %.1fs total",
        len(succeeded), len(failed), wall_elapsed,
    )

    return BulkIngestResult(
        total=total,
        succeeded=len(succeeded),
        failed=len(failed),
        skipped=0,
        as_of=date.today(),
        provider=settings.data_provider,
        duration_s=wall_elapsed,
        errors=[{"ticker": r.ticker, "error": r.error} for r in failed],
    )


# ---------------------------------------------------------------------------
# Incremental refresh — only tickers already in DB
# ---------------------------------------------------------------------------

def run_refresh_existing(*, workers: int = 8, **kwargs) -> BulkIngestResult:
    """Re-ingest all tickers already present in the Asset table."""
    from sqlalchemy import select
    from app.models.asset import Asset

    db = SessionLocal()
    try:
        tickers = [row.ticker for row in db.execute(select(Asset.ticker)).scalars().all()]
    finally:
        db.close()

    logger.info("Refreshing %d existing tickers", len(tickers))
    return run_bulk_ingest(tickers=tickers, workers=workers, **kwargs)
