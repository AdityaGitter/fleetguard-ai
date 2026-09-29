"""Pydantic schemas (API contract), decoupled from the ORM models."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
import re

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class VehicleOut(BaseModel):
    vin: str
    fleet_id: UUID
    vehicle_type: str
    make: str
    model: str
    model_year: int
    status: str

    class Config:
        from_attributes = True


class Page(BaseModel):
    """Keyset-pagination envelope used by every list endpoint."""
    items: list
    next_cursor: Optional[str] = None
    limit: int


class TelemetryPoint(BaseModel):
    ts: datetime
    lat: float
    lon: float
    speed_kmh: float
    engine_temp_c: float
    oil_life_pct: float
    battery_voltage: float
    soc_pct: Optional[float] = None
    vibration_index: float
    dtc: list[str] = []


class PredictionOut(BaseModel):
    vin: str
    risk_score: float = Field(..., ge=0, le=1)
    risk_band: str  # low | medium | high | critical
    top_factors: list[dict]
    model_version: str
    scored_at: datetime


class AlertOut(BaseModel):
    alert_id: int
    vin: str
    alert_type: str
    severity: str
    risk_score: Optional[float]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class AlertAck(BaseModel):
    acknowledged_by: str


class TokenRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
