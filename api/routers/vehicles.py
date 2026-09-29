import base64
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import require_role, rate_limit_dependency
from ..database import get_db
from ..models import Vehicle
from ..schemas import VehicleOut, Page

router = APIRouter(prefix="/vehicles", tags=["vehicles"], dependencies=[Depends(rate_limit_dependency)])


def _encode_cursor(vin: str) -> str:
    return base64.urlsafe_b64encode(vin.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor.encode()).decode()


@router.get("", response_model=Page)
def list_vehicles(
    limit: int = Query(50, le=200),
    cursor: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    claims: dict = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    """Keyset (cursor) pagination — avoids the OFFSET-scan cost of page-number
    pagination once a fleet has hundreds of thousands of vehicles."""
    q = db.query(Vehicle).filter(Vehicle.fleet_id == claims["fleet_id"])  # tenant isolation
    if status_filter:
        q = q.filter(Vehicle.status == status_filter)
    if cursor:
        q = q.filter(Vehicle.vin > _decode_cursor(cursor))
    rows = q.order_by(Vehicle.vin).limit(limit + 1).all()

    next_cursor = None
    if len(rows) > limit:
        next_cursor = _encode_cursor(rows[limit - 1].vin)
        rows = rows[:limit]

    return Page(items=[VehicleOut.model_validate(r) for r in rows],
                next_cursor=next_cursor, limit=limit)


@router.get("/{vin}", response_model=VehicleOut)
def get_vehicle(vin: str, claims: dict = Depends(require_role("viewer")), db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.vin == vin, Vehicle.fleet_id == claims["fleet_id"]).first()
    if not v:
        raise HTTPException(status_code=404, detail="vehicle not found")
    return v
