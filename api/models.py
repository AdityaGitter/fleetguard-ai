"""SQLAlchemy ORM models mirroring db/postgres_schema.sql."""

import uuid
from datetime import datetime

from sqlalchemy import (Column, String, Integer, SmallInteger, Numeric, ForeignKey,
                         DateTime, CheckConstraint, JSON, BigInteger)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Fleet(Base):
    __tablename__ = "fleet"
    fleet_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    segment = Column(String(40), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    vehicles = relationship("Vehicle", back_populates="fleet")


class Driver(Base):
    __tablename__ = "driver"
    driver_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleet.fleet_id"), nullable=False)
    name = Column(String(200), nullable=False)
    license_no = Column(String(20), unique=True, nullable=False)
    safety_score = Column(Numeric(4, 1), default=100.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Vehicle(Base):
    __tablename__ = "vehicle"
    vin = Column(String(17), primary_key=True)
    fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleet.fleet_id"), nullable=False)
    driver_id = Column(UUID(as_uuid=True), ForeignKey("driver.driver_id"), nullable=True)
    vehicle_type = Column(String(10), nullable=False)
    make = Column(String(60), nullable=False)
    model = Column(String(60), nullable=False)
    model_year = Column(SmallInteger, nullable=False)
    odo_km_baseline = Column(Numeric(10, 1), default=0)
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)

    fleet = relationship("Fleet", back_populates="vehicles")
    alerts = relationship("Alert", back_populates="vehicle")

    __table_args__ = (
        CheckConstraint("vehicle_type IN ('ICE','HYBRID','EV')", name="ck_vehicle_type"),
    )


class Alert(Base):
    __tablename__ = "alert"
    alert_id = Column(BigInteger, primary_key=True, autoincrement=True)
    vin = Column(String(17), ForeignKey("vehicle.vin"), nullable=False)
    alert_type = Column(String(40), nullable=False)
    severity = Column(String(10), nullable=False)
    risk_score = Column(Numeric(5, 4))
    details = Column(JSON, default=dict)
    status = Column(String(20), default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String(100), nullable=True)

    vehicle = relationship("Vehicle", back_populates="alerts")


class AppUser(Base):
    __tablename__ = "app_user"
    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fleet_id = Column(UUID(as_uuid=True), ForeignKey("fleet.fleet_id"), nullable=False)
    email = Column(String(200), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)  # admin | fleet_manager | viewer
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    audit_id = Column(BigInteger, primary_key=True, autoincrement=True)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_type = Column(String(20), nullable=False)
    action = Column(String(100), nullable=False)
    resource = Column(String(200))
    metadata_ = Column("metadata", JSON, default=dict)
    occurred_at = Column(DateTime, default=datetime.utcnow)
