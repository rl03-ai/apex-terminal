import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Asset(Base):
    __tablename__ = 'assets'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ticker: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    exchange: Mapped[str] = mapped_column(String, default='NASDAQ')
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String, default='USD')
    asset_type: Mapped[str] = mapped_column(String, default='equity')
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssetPriceDaily(Base):
    __tablename__ = 'asset_prices_daily'
    __table_args__ = (UniqueConstraint('asset_id', 'date', name='uq_asset_price_daily'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adjusted_close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String, default='manual')


class AssetFundamentalsQuarterly(Base):
    __tablename__ = 'asset_fundamentals_quarterly'
    __table_args__ = (UniqueConstraint('asset_id', 'fiscal_year', 'fiscal_quarter', name='uq_asset_fundamental_quarter'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    fiscal_period: Mapped[str] = mapped_column(String)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    fiscal_quarter: Mapped[int] = mapped_column(Integer)
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps: Mapped[float | None] = mapped_column(Float, nullable=True)
    free_cash_flow: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_and_equivalents: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_debt: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares_outstanding: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default='manual')
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetEvent(Base):
    __tablename__ = 'asset_events'

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetTechnicalSnapshot(Base):
    __tablename__ = 'asset_technical_snapshots'
    __table_args__ = (UniqueConstraint('asset_id', 'date', name='uq_asset_technical_snapshot'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    ma50: Mapped[float | None] = mapped_column(Float, nullable=True)
    ma200: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi14: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_strength_3m: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_strength_6m: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_52w_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_avg_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_state: Mapped[str | None] = mapped_column(String, nullable=True)


class AssetScoreDaily(Base):
    __tablename__ = 'asset_scores_daily'
    __table_args__ = (UniqueConstraint('asset_id', 'date', name='uq_asset_score_daily'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    growth_score: Mapped[float] = mapped_column(Float)
    quality_score: Mapped[float] = mapped_column(Float)
    narrative_score: Mapped[float] = mapped_column(Float)
    market_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    total_score: Mapped[float] = mapped_column(Float, index=True)
    consistency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_momentum: Mapped[float | None] = mapped_column(Float, nullable=True)
    conviction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    valuation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_percentile: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    score_slope_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_slope_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_trajectory: Mapped[str | None] = mapped_column(String, nullable=True)
    score_regime: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ScannerResult(Base):
    __tablename__ = 'scanner_results'
    __table_args__ = (UniqueConstraint('date', 'scanner_type', 'asset_id', name='uq_scanner_result'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    date: Mapped[date] = mapped_column(Date, index=True)
    scanner_type: Mapped[str] = mapped_column(String, index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('assets.id'), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    priority_score: Mapped[float] = mapped_column(Float)
    total_score: Mapped[float] = mapped_column(Float)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    why_selected: Mapped[dict | None] = mapped_column(JSON, nullable=True)
