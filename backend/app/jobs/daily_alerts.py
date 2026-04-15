from __future__ import annotations

import logging
from datetime import date

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def run(as_of: date | None = None) -> dict:
    db = SessionLocal()
    try:
        from app.services.alerts.engine import run_all_alert_checks
        results = run_all_alert_checks(db, as_of=as_of)
        db.commit()
        logger.info("daily_alerts done: %s", results)
        return results
    finally:
        db.close()
