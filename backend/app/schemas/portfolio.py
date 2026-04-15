from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import ORMModel


class PortfolioCreate(BaseModel):
    name: str
    base_currency: str = 'USD'
    user_id: str


class PortfolioOut(ORMModel):
    id: str
    user_id: str
    name: str
    base_currency: str


class PositionCreate(BaseModel):
    ticker: str
    first_buy_date: date
    quantity: float
    avg_cost: float
    invested_amount: float
    position_type: str | None = None
    horizon: str | None = None
    thesis: str | None = None
    invalidation_rules: str | None = None
    target_weight: float | None = None
    max_weight: float | None = None


class PositionOut(ORMModel):
    id: str
    portfolio_id: str
    asset_id: str
    status: str
    first_buy_date: date
    avg_cost: float
    quantity: float
    invested_amount: float
    current_value: float | None = None
    position_type: str | None = None
    horizon: str | None = None
    thesis: str | None = None
    invalidation_rules: str | None = None


class PositionLotCreate(BaseModel):
    buy_date: date
    quantity: float
    price: float
    fees: float = 0
    notes: str | None = None


class PositionLotOut(ORMModel):
    id: str
    position_id: str
    buy_date: date
    quantity: float
    price: float
    fees: float
    notes: str | None = None


class PositionSnapshotOut(ORMModel):
    date: date
    close_price: float
    market_value: float
    pnl: float
    pnl_pct: float
    weight_in_portfolio: float | None = None
    score_total: float | None = None
    thesis_status: str | None = None
    scenario_status: str | None = None


class PositionScenarioOut(ORMModel):
    as_of_date: date
    bear_low: float
    bear_high: float
    base_low: float
    base_high: float
    bull_low: float
    bull_high: float
    bear_probability: float | None = None
    base_probability: float | None = None
    bull_probability: float | None = None
    summary: dict[str, Any] | None = None


class PositionNoteCreate(BaseModel):
    note_type: str
    content: str


class PositionNoteOut(ORMModel):
    id: str
    note_date: datetime
    note_type: str
    content: str
