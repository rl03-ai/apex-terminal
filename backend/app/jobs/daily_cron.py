#!/usr/bin/env python3
"""Daily cron job — runs all data pipeline tasks in sequence.

Designed to be executed by Render Cron Jobs (or any scheduler).
Typically takes 15-25 minutes to complete full cycle.

Steps (in order):
  1. Refresh existing tickers (prices + fundamentals + events)
  2. Expand universe (add up to 500 new tickers per day, until cap)
  3. Compute daily scores for all assets
  4. Refresh all scanners (5 profiles)
  5. Refresh early signals
  6. Refresh insider alerts
  7. Refresh portfolio snapshots
  8. Generate alerts

Usage:
  python -m app.jobs.daily_cron
  python -m app.jobs.daily_cron --skip-expand       (skip universe expansion)
  python -m app.jobs.daily_cron --only scanner      (run only one step)

Each step is wrapped in try/except so a failure in one doesn't stop the rest.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime

logger = logging.getLogger("daily_cron")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,
)


def _timed(name: str, fn) -> bool:
    start = time.time()
    logger.info("▶ START: %s", name)
    try:
        fn()
        elapsed = time.time() - start
        logger.info("✓ OK:    %s (%.1fs)", name, elapsed)
        return True
    except Exception as exc:
        elapsed = time.time() - start
        logger.exception("✗ FAIL:  %s (%.1fs) — %s", name, elapsed, exc)
        return False


def step_refresh_existing() -> None:
    from app.services.ingestion.bulk import run_refresh_existing
    workers = int(os.getenv("INGEST_WORKERS", "6"))
    result = run_refresh_existing(workers=workers)
    logger.info("refresh_existing: %s", result)


def step_expand_universe() -> None:
    from app.core.database import SessionLocal
    from app.models.asset import Asset
    from app.services.universe.builder import build_universe
    from app.services.ingestion.bulk import run_bulk_ingest
    from sqlalchemy import func

    cap = int(os.getenv("UNIVERSE_CAP", "1500"))
    batch = int(os.getenv("UNIVERSE_BATCH", "500"))

    db = SessionLocal()
    try:
        current = db.query(func.count(Asset.id)).scalar() or 0
    finally:
        db.close()

    if current >= cap:
        logger.info("Universe cap reached (%d/%d), skip expand", current, cap)
        return

    candidates = build_universe(include_russell1000=True)
    db = SessionLocal()
    try:
        existing = {t for (t,) in db.query(Asset.ticker).all()}
    finally:
        db.close()

    new_tickers = [t for t in candidates if t not in existing][:batch]
    if not new_tickers:
        logger.info("No new tickers to ingest")
        return

    logger.info("Expanding universe: +%d tickers", len(new_tickers))
    result = run_bulk_ingest(
        tickers=new_tickers,
        workers=int(os.getenv("INGEST_WORKERS", "4")),
        inter_ticker_delay=0.5,
    )
    logger.info("expand: %s", result)


def step_scoring() -> None:
    from app.jobs.daily_scoring import run
    workers = int(os.getenv("SCORE_WORKERS", "1"))
    result = run(workers=workers)
    logger.info("scoring: %s", result)


def step_scanner() -> None:
    from app.core.database import SessionLocal
    from app.services.scanner.engine import refresh_all_scanners
    db = SessionLocal()
    try:
        result = refresh_all_scanners(db)
        db.commit()
        logger.info("scanner: %s", result)
    finally:
        db.close()


def step_early_signals() -> None:
    from app.core.database import SessionLocal
    from app.services.scanner.early_signal import refresh_early_signals
    db = SessionLocal()
    try:
        result = refresh_early_signals(db)
        logger.info("early_signals: %s", result)
    finally:
        db.close()


def step_insider_alerts() -> None:
    from app.core.database import SessionLocal
    from app.services.scanner.insider_alert import refresh_insider_alerts
    db = SessionLocal()
    try:
        result = refresh_insider_alerts(db)
        logger.info("insider_alerts: %s", result)
    finally:
        db.close()


def step_portfolio_snapshots() -> None:
    from app.core.database import SessionLocal
    from app.models.portfolio import Portfolio
    from app.services.portfolio.logic import refresh_portfolio_snapshots
    db = SessionLocal()
    try:
        portfolios = db.query(Portfolio).all()
        for p in portfolios:
            try:
                refresh_portfolio_snapshots(db, p)
                db.commit()
            except Exception as exc:
                logger.warning("portfolio %s failed: %s", p.id, exc)
                db.rollback()
        logger.info("portfolio snapshots: %d portfolios processed", len(portfolios))
    finally:
        db.close()


STEPS = {
    "refresh":   step_refresh_existing,
    "expand":    step_expand_universe,
    "scoring":   step_scoring,
    "scanner":   step_scanner,
    "early":     step_early_signals,
    "insider":   step_insider_alerts,
    "portfolio": step_portfolio_snapshots,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Run only one step: refresh, expand, scoring, scanner, early, insider, portfolio")
    parser.add_argument("--skip", nargs="+", default=[], help="Skip specific steps")
    parser.add_argument("--skip-expand", action="store_true", help="Shortcut to skip expansion")
    args = parser.parse_args()

    started_at = datetime.utcnow()
    logger.info("=" * 60)
    logger.info("Daily cron starting at %s UTC", started_at.isoformat())
    logger.info("=" * 60)

    if args.only:
        if args.only not in STEPS:
            logger.error("Unknown step: %s. Valid: %s", args.only, list(STEPS.keys()))
            return 2
        ok = _timed(args.only, STEPS[args.only])
        return 0 if ok else 1

    skip = set(args.skip)
    if args.skip_expand:
        skip.add("expand")

    results: dict[str, bool] = {}
    for name, fn in STEPS.items():
        if name in skip:
            logger.info("⊘ SKIP:  %s", name)
            continue
        results[name] = _timed(name, fn)

    elapsed = (datetime.utcnow() - started_at).total_seconds()
    logger.info("=" * 60)
    logger.info("Daily cron finished in %.0fs", elapsed)
    ok_count = sum(1 for v in results.values() if v)
    logger.info("Results: %d/%d succeeded", ok_count, len(results))
    for name, ok in results.items():
        logger.info("  %s %s", "✓" if ok else "✗", name)
    logger.info("=" * 60)

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
