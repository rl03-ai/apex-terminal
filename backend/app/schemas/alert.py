from datetime import datetime
from typing import Any

from app.schemas.common import ORMModel


class AlertOut(ORMModel):
    id: str
    alert_type: str
    severity: str
    title: str
    message: str
    payload: dict[str, Any] | None = None
    is_read: bool
    created_at: datetime
    asset_id: str | None = None
    position_id: str | None = None
    portfolio_id: str | None = None
