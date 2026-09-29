"""
main.py
-------
FastAPI application entrypoint.

Run (after `pip install -r requirements.txt` and a Postgres instance is up):
    uvicorn api.main:app --reload --port 8000

Docs at http://localhost:8000/docs
"""

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .auth import create_access_token, verify_password
from .database import get_db
from .models import AppUser
from .schemas import TokenRequest, TokenResponse
from .routers import vehicles, alerts, predictions

app = FastAPI(
    title="Connected Vehicle Intelligence — Predictive Maintenance API",
    version="0.1.0",
    description="MVP API for the predictive-maintenance problem space "
                 "(hackathon reference: Motorq / Connected Vehicle Intelligence).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to the UI's origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vehicles.router)
app.include_router(alerts.router)
app.include_router(predictions.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/token", response_model=TokenResponse)
def login(body: TokenRequest, db: Session = Depends(get_db)):
    user = db.query(AppUser).filter(AppUser.email == body.email).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = create_access_token(sub=str(user.user_id), fleet_id=str(user.fleet_id), role=user.role)
    return TokenResponse(access_token=token, role=user.role)
