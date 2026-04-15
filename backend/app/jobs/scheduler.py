"""Daily job scheduler using APScheduler.

Runs inside the same FastAPI process. Jobs execute sequentially in a
background thread to avoid DB contention.

Schedule (UTC, configurable via env):
  23:00  daily_universe_ingest   — refresh all tickers in DB (yfinance)
  23:00  daily_bulk_ingest       — ingest full universe (runs after initial setup)
  01:00  daily_scoring           — recompute scores for all assets
  01:30  daily_scanner           — run all scanner profiles
  02:00  daily_portfolio_snaps   — refresh portfolio snapshots

Env vars (optional overrides):
  SCHEDULER_INGEST_HOUR     (default 23)
  SCHEDULER_SCORING_HOUR    (default 1)
  SCHEDULER_SCANNER_HOUR    (default 1)
  SCHEDULER_SCANNER_MINUTE  (default 30)
  SCHEDULER_PORTFOLIO_HOUR  (default 2)
  SCHEDULER_TIMEZONE        (default UTC)
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
# Job wrappers
# ---------------------------------------------------------------------------

def _job_refresh_existing() -> None:
    """Re-ingest all tickers already in the DB."""
    try:
        from app.services.ingestion.bulk import run_refresh_existing
        result = run_refresh_existing(workers=int(os.getenv("INGEST_WORKERS", "8")))
        logger.info("daily_refresh done — %s", result)
    except Exception as exc:
        logger.exception("daily_refresh FAILED: %s", exc)


def _job_scoring() -> None:
    try:
        from app.jobs.daily_scoring import run as run_scoring
        result = run_scoring()
        logger.info("daily_scoring done — %s", result)
    except Exception as exc:
        logger.exception("daily_scoring FAILED: %s", exc)


def _job_scanner() -> None:
    try:
        from app.jobs.daily_scanner import run as run_scanner
        result = run_scanner()
        logger.info("daily_scanner done — %s", result)
    except Exception as exc:
        logger.exception("daily_scanner FAILED: %s", exc)


def _job_portfolio_snapshots() -> None:
    try:
        from app.jobs.daily_portfolio_snapshots import run as run_snaps
        result = run_snaps()
        logger.info("daily_portfolio_snapshots done — %s", result)
    except Exception as exc:
        logger.exception("daily_portfolio_snapshots FAILED: %s", exc)


def _job_alerts() -> None:
    try:
        from app.jobs.daily_alerts import run as run_alerts
        result = run_alerts()
        logger.info("daily_alerts done — %s", result)
    except Exception as exc:
        logger.exception("daily_alerts FAILED: %s", exc)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def start_scheduler() -> BackgroundScheduler:
    global _scheduler

    tz = os.getenv("SCHEDULER_TIMEZONE", "UTC")

    ingest_hour = _env_int("SCHEDULER_INGEST_HOUR", 23)
    scoring_hour = _env_int("SCHEDULER_SCORING_HOUR", 1)
    scanner_hour = _env_int("SCHEDULER_SCANNER_HOUR", 1)
    scanner_minute = _env_int("SCHEDULER_SCANNER_MINUTE", 30)
    portfolio_hour = _env_int("SCHEDULER_PORTFOLIO_HOUR", 2)

    scheduler = BackgroundScheduler(timezone=tz)

    # 23:00 — refresh all existing tickers with fresh yfinance data
    scheduler.add_job(
        _job_refresh_existing,
        CronTrigger(hour=ingest_hour, minute=0, timezone=tz),
        id="daily_refresh",
        name="Daily universe refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 01:00 — recompute scores
    scheduler.add_job(
        _job_scoring,
        CronTrigger(hour=scoring_hour, minute=0, timezone=tz),
        id="daily_scoring",
        name="Daily scoring",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 01:30 — run scanner profiles
    scheduler.add_job(
        _job_scanner,
        CronTrigger(hour=scanner_hour, minute=scanner_minute, timezone=tz),
        id="daily_scanner",
        name="Daily scanner",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 02:00 — portfolio snapshots
    scheduler.add_job(
        _job_portfolio_snapshots,
        CronTrigger(hour=portfolio_hour, minute=0, timezone=tz),
        id="daily_portfolio_snapshots",
        name="Daily portfolio snapshots",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # 02:30 — alerts
    scheduler.add_job(
        _job_alerts,
        CronTrigger(hour=portfolio_hour, minute=30, timezone=tz),
        id="daily_alerts",
        name="Daily alerts",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    _scheduler = scheduler

    jobs = scheduler.get_jobs()
    logger.info("Scheduler started — %d jobs scheduled (tz=%s)", len(jobs), tz)
    for job in jobs:
        logger.info("  • %s next_run=%s", job.name, job.next_run_time)

    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
