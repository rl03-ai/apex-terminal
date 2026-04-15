import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Portfolio(Base):
    __tablename__ = 'portfolios'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String)
    base_currency: Mapped[str] = mapped_column(String, default='USD')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Position(Base):
    __tablename__ = 'positions'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    portfolio_id: Mapped[str] = mapped_column(ForeignKey('portfolios.id'), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    status: Mapped[str] = mapped_column(String, default='open')
    first_buy_date: Mapped[date] = mapped_column(Date)
    avg_cost: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    invested_amount: Mapped[float] = mapped_column(Float)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_type: Mapped[str | None] = mapped_column(String, nullable=True)
    horizon: Mapped[str | None] = mapped_column(String, nullable=True)
    thesis: Mapped[str | None] = mapped_column(String, nullable=True)
    invalidation_rules: Mapped[str | None] = mapped_column(String, nullable=True)
    target_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PositionLot(Base):
    __tablename__ = 'position_lots'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    position_id: Mapped[str] = mapped_column(ForeignKey('positions.id'), index=True)
    buy_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PositionSnapshotDaily(Base):
    __tablename__ = 'position_snapshots_daily'
    __table_args__ = (UniqueConstraint('position_id', 'date', name='uq_position_snapshot_daily'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    position_id: Mapped[str] = mapped_column(ForeignKey('positions.id'), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    close_price: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    weight_in_portfolio: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_narrative: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_market: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    thesis_status: Mapped[str | None] = mapped_column(String, nullable=True)
    scenario_status: Mapped[str | None] = mapped_column(String, nullable=True)


class PositionNote(Base):
    __tablename__ = 'position_notes'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    position_id: Mapped[str] = mapped_column(ForeignKey('positions.id'), index=True)
    note_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    note_type: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PositionScenario(Base):
    __tablename__ = 'position_scenarios'
    __table_args__ = (UniqueConstraint('position_id', 'as_of_date', name='uq_position_scenario'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    position_id: Mapped[str] = mapped_column(ForeignKey('positions.id'), index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    bear_low: Mapped[float] = mapped_column(Float)
    bear_high: Mapped[float] = mapped_column(Float)
    base_low: Mapped[float] = mapped_column(Float)
    base_high: Mapped[float] = mapped_column(Float)
    bull_low: Mapped[float] = mapped_column(Float)
    bull_high: Mapped[float] = mapped_column(Float)
    bear_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    bull_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Alert(Base):
    __tablename__ = 'alerts'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), index=True, nullable=True)
    portfolio_id: Mapped[str | None] = mapped_column(ForeignKey('portfolios.id'), nullable=True)
    position_id: Mapped[str | None] = mapped_column(ForeignKey('positions.id'), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey('assets.id'), nullable=True)
    alert_type: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, default='info')
    title: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
