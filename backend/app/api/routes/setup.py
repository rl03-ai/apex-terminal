"""One-time setup endpoint — seeds demo data on first run."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_db

router = APIRouter()

@router.post("/setup/seed", summary="Seed demo data (run once after deploy)")
def seed(db: Session = Depends(get_db)) -> dict:
    import uuid
    from app.core.database import Base, engine
    import app.models.asset, app.models.portfolio, app.models.user
    Base.metadata.create_all(bind=engine)

    # Create demo user if none exists
    from app.models.user import User
    user = db.query(User).first()
    if not user:
        user = User(
            id=str(uuid.uuid4()),
            email="demo@apex-terminal.io",
            name="Demo User",
            hashed_password="demo",
        )
        db.add(user)
        db.flush()

    # Seed assets if DB is empty
    from app.models.asset import Asset
    if db.query(Asset).count() == 0:
        from app.services.ingestion.logic import ingest_ticker
        from app.services.ingestion.providers import DemoMarketDataProvider
        provider = DemoMarketDataProvider()
        for ticker in ["SOFI", "RKLB", "NOW", "EOSE"]:
            try:
                ingest_ticker(db, provider, ticker)
            except Exception:
                pass
        db.flush()

        from app.services.scoring.engine import refresh_all_scores
        from app.services.scoring.percentile import run_normalisation
        rows = refresh_all_scores(db)
        db.flush()
        if rows:
            run_normalisation(db, as_of=rows[0].date)

    # Create demo portfolio if none exists
    from app.models.portfolio import Portfolio
    portfolio = db.query(Portfolio).first()
    if not portfolio:
        portfolio = Portfolio(
            id=str(uuid.uuid4()),
            user_id=user.id,
            name="Demo Portfolio",
            base_currency="USD",
        )
        db.add(portfolio)

    db.commit()

    assets = db.query(Asset).all()
    return {
        "status": "ready",
        "user": user.email,
        "assets": [a.ticker for a in assets],
        "portfolio": portfolio.name,
    }
