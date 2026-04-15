"""Alerts API routes.

GET  /alerts                      — list alerts (system + user)
GET  /alerts/summary              — counts by type and severity
POST /alerts/{alert_id}/read      — mark as read
POST /alerts/read-all             — mark all as read
DELETE /alerts/{alert_id}         — delete an alert
POST /alerts/run                  — manually trigger alert generation
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.portfolio import Alert
from app.schemas.alert import AlertOut

router = APIRouter()


@router.get('', response_model=list[AlertOut])
def list_alerts(
    severity: str | None = None,
    alert_type: str | None = None,
    unread_only: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[Alert]:
    """List alerts. No auth required — returns system + user alerts."""
    q = db.query(Alert).order_by(desc(Alert.created_at))
    if severity:
        q = q.filter(Alert.severity == severity)
    if alert_type:
        q = q.filter(Alert.alert_type == alert_type)
    if unread_only:
        q = q.filter(Alert.is_read.is_(False))
    return q.limit(limit).all()


@router.get('/summary', summary='Alert counts by type and severity')
def alert_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return counts of unread alerts grouped by type and severity."""
    rows = (
        db.query(Alert.alert_type, Alert.severity, func.count(Alert.id))
        .filter(Alert.is_read.is_(False))
        .group_by(Alert.alert_type, Alert.severity)
        .all()
    )
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for alert_type, severity, count in rows:
        by_type[alert_type] = by_type.get(alert_type, 0) + count
        by_severity[severity] = by_severity.get(severity, 0) + count

    return {
        'total_unread': sum(by_severity.values()),
        'by_type': by_type,
        'by_severity': by_severity,
    }


@router.post('/{alert_id}/read', summary='Mark alert as read')
def mark_read(alert_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail='Alert not found')
    alert.is_read = True
    db.commit()
    return {'status': 'ok', 'alert_id': alert_id}


@router.post('/read-all', summary='Mark all alerts as read')
def mark_all_read(db: Session = Depends(get_db)) -> dict[str, int]:
    count = db.query(Alert).filter(Alert.is_read.is_(False)).update({'is_read': True})
    db.commit()
    return {'marked_read': count}


@router.delete('/{alert_id}', summary='Delete an alert')
def delete_alert(alert_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail='Alert not found')
    db.delete(alert)
    db.commit()
    return {'status': 'deleted', 'alert_id': alert_id}


@router.post('/run', summary='Manually trigger alert generation')
def run_alerts(
    as_of: date | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run all alert generators immediately."""
    from app.services.alerts.engine import run_all_alert_checks
    results = run_all_alert_checks(db, as_of=as_of)
    db.commit()
    return {'status': 'completed', 'results': results}
