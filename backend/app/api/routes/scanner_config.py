"""Scanner configuration endpoints.

GET /scanner/config          — list all scanner thresholds
PUT /scanner/config/{type}   — update thresholds for a scanner type
POST /scanner/config/reset   — reset all thresholds to defaults
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.scanner_config import ScannerConfig, DEFAULT_THRESHOLDS, seed_scanner_config

router = APIRouter()


class ThresholdUpdate(BaseModel):
    min_total:     float | None = None
    min_growth:    float | None = None
    min_quality:   float | None = None
    min_narrative: float | None = None
    min_market:    float | None = None
    max_risk:      float | None = None


def _row_to_dict(row: ScannerConfig) -> dict:
    return {
        'scanner_type':  row.scanner_type,
        'min_total':     row.min_total,
        'min_growth':    row.min_growth,
        'min_quality':   row.min_quality,
        'min_narrative': row.min_narrative,
        'min_market':    row.min_market,
        'max_risk':      row.max_risk,
        'updated_at':    row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get('/config', summary='List all scanner thresholds')
def get_config(db: Session = Depends(get_db)) -> list[dict]:
    seed_scanner_config(db)
    rows = db.query(ScannerConfig).order_by(ScannerConfig.scanner_type).all()
    return [_row_to_dict(r) for r in rows]


@router.put('/config/{scanner_type}', summary='Update thresholds for a scanner type')
def update_config(
    scanner_type: str,
    payload: ThresholdUpdate,
    db: Session = Depends(get_db),
) -> dict:
    seed_scanner_config(db)
    row = db.query(ScannerConfig).filter(ScannerConfig.scanner_type == scanner_type).first()
    if not row:
        raise HTTPException(status_code=404, detail=f'Scanner type {scanner_type!r} not found')

    if payload.min_total     is not None: row.min_total     = payload.min_total
    if payload.min_growth    is not None: row.min_growth    = payload.min_growth
    if payload.min_quality   is not None: row.min_quality   = payload.min_quality
    if payload.min_narrative is not None: row.min_narrative = payload.min_narrative
    if payload.min_market    is not None: row.min_market    = payload.min_market
    if payload.max_risk      is not None: row.max_risk      = payload.max_risk

    db.commit()
    db.refresh(row)

    # Apply to in-memory profiles immediately (no restart needed)
    from app.services.scanner.engine import SCANNER_PROFILES
    profile = SCANNER_PROFILES.get(scanner_type)
    if profile:
        profile.min_total     = row.min_total
        if row.min_growth    is not None: profile.min_growth    = row.min_growth
        if row.min_quality   is not None: profile.min_quality   = row.min_quality
        if row.min_narrative is not None: profile.min_narrative = row.min_narrative
        if row.min_market    is not None: profile.min_market    = row.min_market
        if row.max_risk      is not None: profile.max_risk      = row.max_risk

    return _row_to_dict(row)


@router.post('/config/reset', summary='Reset all thresholds to defaults')
def reset_config(db: Session = Depends(get_db)) -> dict:
    db.query(ScannerConfig).delete()
    db.commit()
    seed_scanner_config(db)

    # Reset in-memory profiles
    from app.services.scanner.engine import SCANNER_PROFILES
    for scanner_type, thresholds in DEFAULT_THRESHOLDS.items():
        profile = SCANNER_PROFILES.get(scanner_type)
        if profile:
            for k, v in thresholds.items():
                setattr(profile, k, v)

    return {'status': 'reset', 'defaults': DEFAULT_THRESHOLDS}
