from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ORMModel


class AssetOut(ORMModel):
    id: str
    ticker: str
    name: str
    exchange: str
    sector: str | None = None
    industry: str | None = None
    currency: str
    market_cap: float | None = None


class AssetScoreOut(ORMModel):
    date: date
    growth_score: float
    quality_score: float
    narrative_score: float
    market_score: float
    risk_score: float
    total_score: float
    consistency_score: float | None = None
    score_momentum: float | None = None
    conviction_score: float | None = None
    state: str | None = None
    explanation: dict[str, Any] | None = None


class AssetEventOut(ORMModel):
    id: str
    event_type: str
    event_date: datetime
    title: str
    summary: str | None = None
    sentiment_score: float | None = None
    importance_score: float | None = None


class PricePoint(BaseModel):
    date: date
    close: float
