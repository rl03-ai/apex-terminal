"""Watchlist — user-marked favorite assets.

Simple model: one row per (user, asset) pair.
Used as a filter in the decision matrix and shown as a star icon.
"""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
import uuid


class Watchlist(Base):
    __tablename__ = 'watchlist'
    __table_args__ = (UniqueConstraint('user_id', 'asset_id', name='uq_watchlist_user_asset'),)

    id: Mapped[str]            = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str]        = mapped_column(ForeignKey('users.id'), index=True)
    asset_id: Mapped[str]       = mapped_column(ForeignKey('assets.id'), index=True)
    notes: Mapped[str | None]   = mapped_column(String, nullable=True)
    added_at: Mapped[datetime]  = mapped_column(DateTime(timezone=True), server_default=func.now())
