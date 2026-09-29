import pickle
from datetime import datetime
from pathlib import Path
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_role, rate_limit_dependency
from ..database import get_db
from ..models import Vehicle
from ..schemas import PredictionOut

router = APIRouter(prefix="/predictions", tags=["predictions"],
                    dependencies=[Depends(rate_limit_dependency)])

MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "ml" / "artifacts" / "model.pkl"


@lru_cache(maxsize=1)
def _load_model():
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


def _risk_band(score: float) -> str:
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.2:
        return "medium"
    return "low"


@router.get("/{vin}", response_model=PredictionOut)
def get_prediction(vin: str, claims: dict = Depends(require_role("viewer")), db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.vin == vin, Vehicle.fleet_id == claims["fleet_id"]).first()
    if not v:
        raise HTTPException(status_code=404, detail="vehicle not found")

    bundle = _load_model()
    model, feature_cols = bundle["model"], bundle["feature_cols"]

    # In production this reads the vehicle's latest feature row from the
    # feature store (materialised by the streaming job); the MVP demo
    # substitutes the vehicle's most recent 5-min telemetry rollup, fetched
    # from `telemetry_5min` in TimescaleDB (see ingestion/consumer.py).
    latest_features = _fetch_latest_features(db, vin)  # dict of feature_cols -> value
    ordered = [[latest_features.get(c, 0.0) for c in feature_cols]]
    score = float(model.predict_proba(ordered)[0][1])

    importances = sorted(zip(feature_cols, model.feature_importances_), key=lambda x: -x[1])[:3]
    top_factors = [{"feature": name, "importance": round(float(imp), 3),
                     "value": latest_features.get(name)} for name, imp in importances]

    return PredictionOut(
        vin=vin, risk_score=round(score, 4), risk_band=_risk_band(score),
        top_factors=top_factors, model_version="gbdt-v1", scored_at=datetime.utcnow(),
    )


def _fetch_latest_features(db: Session, vin: str) -> dict:
    """Placeholder for the real TimescaleDB continuous-aggregate lookup.
    Wired up in ingestion/consumer.py once the pipeline is running against
    a live TimescaleDB instance; kept isolated here so it's a one-line swap."""
    row = db.execute(
        "SELECT avg_speed_kmh, max_engine_temp_c, min_oil_life_pct, avg_battery_voltage, "
        "max_vibration_index, dtc_event_count, 0 as harsh_brake_ct, 0 as odo_km, "
        "max_engine_temp_c as max_engine_temp_c_roll3, min_oil_life_pct as min_oil_life_pct_roll3, "
        "avg_battery_voltage as avg_battery_voltage_roll3, max_vibration_index as max_vibration_index_roll3 "
        "FROM telemetry_5min WHERE vin = :vin ORDER BY bucket DESC LIMIT 1",
        {"vin": vin},
    ).mappings().first()
    return dict(row) if row else {}
