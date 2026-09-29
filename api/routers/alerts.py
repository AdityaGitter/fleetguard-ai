from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import require_role, rate_limit_dependency
from ..database import get_db
from ..models import Alert, Vehicle, AuditLog
from ..schemas import AlertOut, AlertAck, Page

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(rate_limit_dependency)])


@router.get("", response_model=Page)
def list_alerts(
    severity: Optional[str] = None,
    status_filter: Optional[str] = Query("open", alias="status"),
    limit: int = Query(50, le=200),
    offset: int = 0,
    claims: dict = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    q = (
        db.query(Alert)
        .join(Vehicle, Alert.vin == Vehicle.vin)
        .filter(Vehicle.fleet_id == claims["fleet_id"])
    )
    if severity:
        q = q.filter(Alert.severity == severity)
    if status_filter:
        q = q.filter(Alert.status == status_filter)
    rows = q.order_by(Alert.created_at.desc()).offset(offset).limit(limit).all()
    return Page(items=[AlertOut.model_validate(r) for r in rows], limit=limit)


@router.post("/{alert_id}/ack", response_model=AlertOut)
def acknowledge_alert(
    alert_id: int,
    body: AlertAck,
    claims: dict = Depends(require_role("fleet_manager")),
    db: Session = Depends(get_db),
):
    alert = (
        db.query(Alert)
        .join(Vehicle, Alert.vin == Vehicle.vin)
        .filter(Alert.alert_id == alert_id, Vehicle.fleet_id == claims["fleet_id"])
        .first()
    )
    if not alert:
        raise HTTPException(status_code=404, detail="alert not found")

    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.utcnow()
    alert.acknowledged_by = body.acknowledged_by

    db.add(AuditLog(
        actor_id=claims.get("sub"), actor_type="user", action="alert.acknowledge",
        resource=f"alert/{alert_id}", metadata_={"fleet_id": str(claims["fleet_id"])},
    ))
    db.commit()
    db.refresh(alert)
    return alert
