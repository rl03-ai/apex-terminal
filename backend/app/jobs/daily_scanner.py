from __future__ import annotations

from datetime import date

from app.core.database import SessionLocal
from app.services.scanner.engine import refresh_all_scanners


def run(as_of: date | None = None) -> dict[str, int]:
    db = SessionLocal()
    try:
        summary = refresh_all_scanners(db, as_of=as_of)
        db.commit()
        return summary
    finally:
        db.close()
