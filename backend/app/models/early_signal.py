"""Early signal tracking model.

Tracks when an asset first entered the "early signal" zone and at what price.
Signal stays active while:
  - Price hasn't moved more than 10% from first_detected_price
  - Criteria still pass (refreshed nightly)

When price moves >10%, marked as 'exited' (movement already started).
"""

from __future__ import annotations
from datetime import datetime, date
from sqlalchemy import Boolean, DateTime, Date, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
import uuid


class EarlySignal(Base):
    __tablename__ = 'early_signals'

    id: Mapped[str]                = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str]           = mapped_column(ForeignKey('assets.id'), index=True)
    first_detected_date: Mapped[date] = mapped_column(Date)
    first_detected_price: Mapped[float] = mapped_column(Float)
    last_signal_date: Mapped[date]  = mapped_column(Date)
    current_price: Mapped[float]    = mapped_column(Float)
    pct_move_since: Mapped[float]   = mapped_column(Float, default=0.0)
    signal_score: Mapped[float]     = mapped_column(Float)
    criteria_passed: Mapped[str]    = mapped_column(String)  # comma-separated list
    total_score: Mapped[float]      = mapped_column(Float)
    is_active: Mapped[bool]         = mapped_column(Boolean, default=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
