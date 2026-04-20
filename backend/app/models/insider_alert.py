"""Insider alert cache.

Stores the result of the insider alerts scanner.
Refreshed nightly by scheduler.
"""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
import uuid


class InsiderAlertCache(Base):
    __tablename__ = 'insider_alerts_cache'

    id: Mapped[str]                    = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str]               = mapped_column(ForeignKey('assets.id'), index=True)
    signal_type: Mapped[str]            = mapped_column(String)
    dollar_amount: Mapped[float]        = mapped_column(Float)
    num_insiders: Mapped[int]           = mapped_column(Integer)
    num_transactions: Mapped[int]       = mapped_column(Integer)
    largest_single: Mapped[float]       = mapped_column(Float)
    most_recent_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_score: Mapped[float]          = mapped_column(Float)
    details: Mapped[str]                = mapped_column(String)
    created_at: Mapped[datetime]        = mapped_column(DateTime(timezone=True), server_default=func.now())
