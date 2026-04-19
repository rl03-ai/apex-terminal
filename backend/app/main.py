import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import alerts, assets, auth, backtest, catalyst, health, portfolios, positions, scanner, scanner_config, setup, universe, xbrl
from app.core.config import get_settings
from app.core.database import Base, engine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(health.router)
app.include_router(auth.router, prefix='/auth', tags=['auth'])
app.include_router(assets.router, prefix='/assets', tags=['assets'])
app.include_router(scanner.router, prefix='/scanner', tags=['scanner'])
app.include_router(portfolios.router, prefix='/portfolios', tags=['portfolios'])
app.include_router(positions.router, prefix='/positions', tags=['positions'])
app.include_router(alerts.router, prefix='/alerts', tags=['alerts'])
app.include_router(universe.router, prefix='/universe', tags=['universe'])
app.include_router(backtest.router, prefix='/backtest', tags=['backtest'])
app.include_router(catalyst.router, prefix='/catalyst', tags=['catalyst'])
app.include_router(xbrl.router, prefix='/xbrl', tags=['xbrl'])
app.include_router(setup.router, tags=['setup'])
app.include_router(scanner_config.router, prefix='/scanner', tags=['scanner-config'])


@app.on_event('startup')
async def _load_scanner_config():
    """Load scanner thresholds from DB on startup."""
    try:
        from app.core.database import SessionLocal
        from app.models.scanner_config import ScannerConfig, seed_scanner_config
        from app.services.scanner.engine import SCANNER_PROFILES
        import app.models.scanner_config  # ensure table created
        from app.core.database import Base, engine
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            seed_scanner_config(db)
            rows = db.query(ScannerConfig).all()
            for row in rows:
                profile = SCANNER_PROFILES.get(row.scanner_type)
                if profile:
                    profile.min_total = row.min_total
                    if row.min_growth    is not None: profile.min_growth    = row.min_growth
                    if row.min_quality   is not None: profile.min_quality   = row.min_quality
                    if row.min_narrative is not None: profile.min_narrative = row.min_narrative
                    if row.min_market    is not None: profile.min_market    = row.min_market
                    if row.max_risk      is not None: profile.max_risk      = row.max_risk
        finally:
            db.close()
    except Exception as e:
        import logging; logging.getLogger(__name__).warning('scanner config load failed: %s', e)

@app.on_event('startup')
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    # Start background scheduler (skip in demo mode to avoid unnecessary network calls)
    if settings.data_provider != 'demo':
        try:
            from app.jobs.scheduler import start_scheduler
            start_scheduler()
            logger.info("Background scheduler started")
        except Exception as exc:
            logger.warning("Scheduler could not start: %s", exc)


@app.on_event('shutdown')
def on_shutdown() -> None:
    try:
        from app.jobs.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
