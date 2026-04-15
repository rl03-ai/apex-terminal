from __future__ import annotations

from app.core.database import SessionLocal
from app.models.portfolio import Portfolio
from app.services.portfolio.logic import refresh_portfolio_snapshots


def run() -> dict[str, int]:
    db = SessionLocal()
    try:
        portfolios = db.query(Portfolio).all()
        processed = 0
        for portfolio in portfolios:
            refresh_portfolio_snapshots(db, portfolio)
            processed += 1
        db.commit()
        return {'portfolios_processed': processed}
    finally:
        db.close()
