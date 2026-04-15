"""One-time setup endpoint — seeds demo data on first run."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.models.asset import Asset

router = APIRouter()

@router.post("/setup/seed", summary="Seed demo data (run once after deploy)")
def seed(db: Session = Depends(get_db)) -> dict:
    # Only seed if DB is empty
    count = db.query(Asset).count()
    if count > 0:
        return {"status": "already_seeded", "assets": count}

    from app.core.database import Base, engine
    import app.models.asset, app.models.portfolio, app.models.user
    Base.metadata.create_all(bind=engine)

    from app.services.ingestion.logic import ingest_ticker
    from app.services.ingestion.providers import DemoMarketDataProvider
    provider = DemoMarketDataProvider()
    results = []
    for ticker in ["SOFI", "RKLB", "NOW", "EOSE"]:
        try:
            r = ingest_ticker(db, provider, ticker)
            results.append(r["ticker"])
        except Exception as e:
            results.append(f"{ticker}:error:{e}")
    db.commit()

    from app.services.scoring.engine import refresh_all_scores
    from app.services.scoring.percentile import run_normalisation
    rows = refresh_all_scores(db)
    db.flush()
    if rows:
        run_normalisation(db, as_of=rows[0].date)
    db.commit()

    return {"status": "seeded", "assets": results}
