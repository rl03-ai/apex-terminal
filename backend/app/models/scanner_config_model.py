"""Scanner configuration model.

Stores scanner profile thresholds in the database so they persist
across restarts and deploys. Each scanner type has its own row.

Default values match the SCANNER_PROFILES in engine.py.
On first run, the table is seeded with defaults if empty.
"""

from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScannerConfig(Base):
    __tablename__ = 'scanner_config'

    scanner_type:    Mapped[str]            = mapped_column(String, primary_key=True)
    min_total:       Mapped[float]           = mapped_column(Float, default=45.0)
    min_growth:      Mapped[float | None]    = mapped_column(Float, nullable=True)
    min_quality:     Mapped[float | None]    = mapped_column(Float, nullable=True)
    min_narrative:   Mapped[float | None]    = mapped_column(Float, nullable=True)
    min_market:      Mapped[float | None]    = mapped_column(Float, nullable=True)
    max_risk:        Mapped[float | None]    = mapped_column(Float, nullable=True)
    updated_at:      Mapped[datetime]        = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


# Default thresholds — match engine.py SCANNER_PROFILES
DEFAULT_THRESHOLDS: dict[str, dict] = {
    'repricing': {
        'min_total': 52.0, 'min_growth': 48.0, 'min_market': 42.0, 'max_risk': 75.0,
    },
    'early_growth': {
        'min_total': 50.0, 'min_growth': 52.0, 'min_quality': 38.0,
    },
    'quality_compounder': {
        'min_total': 52.0, 'min_quality': 55.0, 'max_risk': 70.0,
    },
    'narrative': {
        'min_total': 48.0, 'min_narrative': 50.0, 'min_growth': 40.0,
    },
    'speculative': {
        'min_total': 44.0, 'min_narrative': 44.0, 'max_risk': 90.0,
    },
}


def seed_scanner_config(db) -> None:
    """Seed default thresholds if table is empty."""
    existing = db.query(ScannerConfig).count()
    if existing > 0:
        return
    for scanner_type, thresholds in DEFAULT_THRESHOLDS.items():
        db.add(ScannerConfig(scanner_type=scanner_type, **thresholds))
    db.commit()
