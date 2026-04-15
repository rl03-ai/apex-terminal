from datetime import date
from typing import Any

from app.schemas.common import ORMModel


class ScannerResultOut(ORMModel):
    date: date
    scanner_type: str
    rank: int
    priority_score: float
    total_score: float
    state: str | None = None
    why_selected: dict[str, Any] | None = None
    asset_id: str
    # Enriched fields for frontend filtering
    ticker: str | None = None
    asset_name: str | None = None
    sector: str | None = None
    risk_score: float | None = None
    score_percentile: float | None = None
    score_regime: str | None = None
    valuation_score: float | None = None
    market_cap: float | None = None
