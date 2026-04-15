"""Universe and bulk ingestion API routes.

Endpoints:
  GET  /universe/list           — return the current auto-built ticker list
  GET  /universe/db             — tickers already in the database
  POST /universe/ingest         — ingest full universe (background task)
  POST /universe/ingest/custom  — ingest a custom list of tickers
  GET  /universe/scheduler      — scheduler job status
  POST /universe/scheduler/run/{job_id} — manually trigger a scheduled job
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.asset import Asset

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CustomIngestRequest(BaseModel):
    tickers: list[str]
    workers: int = 8


class BulkIngestResponse(BaseModel):
    status: str
    message: str
    ticker_count: int | None = None


# ---------------------------------------------------------------------------
# Background task wrappers
# ---------------------------------------------------------------------------

def _bg_bulk_ingest(tickers: list[str] | None, workers: int) -> None:
    from app.services.ingestion.bulk import run_bulk_ingest
    try:
        result = run_bulk_ingest(tickers=tickers, workers=workers)
        logger.info("Background bulk ingest finished: %s", result)
    except Exception as exc:
        logger.exception("Background bulk ingest failed: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/list", summary="Return auto-built ticker universe")
def get_universe_list(
    sp500: bool = True,
    nasdaq100: bool = True,
    russell1000: bool = True,
) -> dict[str, Any]:
    """Return the ticker universe that would be ingested."""
    from app.services.universe.builder import build_universe
    tickers = build_universe(
        include_sp500=sp500,
        include_nasdaq100=nasdaq100,
        include_russell1000=russell1000,
    )
    return {"count": len(tickers), "tickers": tickers}


@router.get("/db", summary="Tickers already in the database")
def get_db_universe(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return all tickers currently stored in the Asset table."""
    rows = db.execute(select(Asset.ticker, Asset.name, Asset.sector)).all()
    assets = [{"ticker": r.ticker, "name": r.name, "sector": r.sector} for r in rows]
    return {"count": len(assets), "assets": assets}


@router.post("/ingest", summary="Ingest full auto-built universe in background")
def ingest_full_universe(
    background_tasks: BackgroundTasks,
    workers: int = 8,
    sp500: bool = True,
    nasdaq100: bool = True,
    russell1000: bool = True,
) -> BulkIngestResponse:
    """
    Triggers a background bulk ingest of the full auto-built universe.
    Returns immediately; check logs for progress.
    """
    from app.services.universe.builder import build_universe
    tickers = build_universe(
        include_sp500=sp500,
        include_nasdaq100=nasdaq100,
        include_russell1000=russell1000,
    )
    background_tasks.add_task(_bg_bulk_ingest, tickers, workers)
    return BulkIngestResponse(
        status="accepted",
        message=f"Bulk ingest of {len(tickers)} tickers started in background with {workers} workers.",
        ticker_count=len(tickers),
    )


@router.post("/ingest/custom", summary="Ingest a custom list of tickers")
def ingest_custom(
    body: CustomIngestRequest,
    background_tasks: BackgroundTasks,
) -> BulkIngestResponse:
    """Ingest a user-supplied list of tickers in background."""
    tickers = [t.upper().strip() for t in body.tickers if t.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="Empty ticker list.")
    background_tasks.add_task(_bg_bulk_ingest, tickers, body.workers)
    return BulkIngestResponse(
        status="accepted",
        message=f"Ingest of {len(tickers)} tickers started with {body.workers} workers.",
        ticker_count=len(tickers),
    )


@router.get("/scheduler", summary="Scheduler job status")
def get_scheduler_status() -> dict[str, Any]:
    """Return the status of all scheduled jobs."""
    try:
        from app.jobs.scheduler import get_scheduler
        scheduler = get_scheduler()
        if scheduler is None or not scheduler.running:
            return {"running": False, "jobs": []}
        jobs = [
            {
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            }
            for job in scheduler.get_jobs()
        ]
        return {"running": True, "jobs": jobs}
    except Exception as exc:
        return {"running": False, "error": str(exc)}


@router.post("/scheduler/run/{job_id}", summary="Manually trigger a scheduled job")
def trigger_job(job_id: str) -> dict[str, str]:
    """Immediately execute a scheduled job by its ID."""
    valid_jobs = {
        "daily_refresh":           "app.jobs.scheduler._job_refresh_existing",
        "daily_scoring":           "app.jobs.scheduler._job_scoring",
        "daily_scanner": "app.jobs.scheduler._job_scanner",
        "daily_portfolio_snapshots": "app.jobs.scheduler._job_portfolio_snapshots",
        "daily_alerts": "app.jobs.scheduler._job_alerts",
    }
    if job_id not in valid_jobs:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_id}'. Valid: {list(valid_jobs)}")

    try:
        from app.jobs import scheduler as sched_module
        fn = getattr(sched_module, f"_job_{job_id.replace('daily_', '')}", None)
        if fn is None:
            raise HTTPException(status_code=404, detail="Job function not found.")
        import threading
        t = threading.Thread(target=fn, daemon=True)
        t.start()
        return {"status": "triggered", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
