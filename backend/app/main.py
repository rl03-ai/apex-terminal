import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import alerts, assets, auth, backtest, catalyst, health, portfolios, positions, scanner, universe, xbrl
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
