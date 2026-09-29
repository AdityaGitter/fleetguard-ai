-- ============================================================================
-- Postgres 3NF relational core
-- Holds data that needs strong (CP) consistency: ownership, subscriptions,
-- billing, RBAC, audit. High-volume telemetry does NOT live here (see
-- timescale_schema.sql) — this avoids write amplification on the OLTP core.
-- ============================================================================

CREATE TABLE fleet (
    fleet_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200) NOT NULL,
    segment         VARCHAR(40) NOT NULL CHECK (segment IN
                        ('commercial','rental','lending_collateral','dealership')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE driver (
    driver_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fleet_id        UUID NOT NULL REFERENCES fleet(fleet_id) ON DELETE CASCADE,
    name            VARCHAR(200) NOT NULL,
    license_no      VARCHAR(20) NOT NULL UNIQUE,
    safety_score    NUMERIC(4,1) DEFAULT 100.0,   -- rolling, updated by scoring job
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_driver_fleet ON driver(fleet_id);

CREATE TABLE vehicle (
    vin             CHAR(17) PRIMARY KEY
                        CHECK (vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),   -- no I,O,Q per VIN spec
    fleet_id        UUID NOT NULL REFERENCES fleet(fleet_id) ON DELETE CASCADE,
    driver_id       UUID REFERENCES driver(driver_id) ON DELETE SET NULL,
    vehicle_type    VARCHAR(10) NOT NULL CHECK (vehicle_type IN ('ICE','HYBRID','EV')),
    make            VARCHAR(60) NOT NULL,
    model           VARCHAR(60) NOT NULL,
    model_year      SMALLINT NOT NULL,
    odo_km_baseline NUMERIC(10,1) NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','in_service','decommissioned')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_vehicle_fleet ON vehicle(fleet_id);
CREATE INDEX idx_vehicle_driver ON vehicle(driver_id);

-- Trips are relatively low-volume derived/rolled-up records (segmented from
-- raw telemetry by the stream processor), so they fit the relational core.
CREATE TABLE trip (
    trip_id         BIGSERIAL PRIMARY KEY,
    vin             CHAR(17) NOT NULL REFERENCES vehicle(vin) ON DELETE CASCADE,
    driver_id       UUID REFERENCES driver(driver_id),
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    distance_km     NUMERIC(8,2),
    harsh_brake_ct  SMALLINT NOT NULL DEFAULT 0,
    idle_seconds    INTEGER NOT NULL DEFAULT 0
);
-- Partitioned by month via a partial index strategy at scale; for MVP a
-- plain btree covers query patterns (see docs/ARCHITECTURE.md for the
-- production partitioning plan).
CREATE INDEX idx_trip_vin_started ON trip(vin, started_at DESC);

CREATE TABLE alert (
    alert_id        BIGSERIAL PRIMARY KEY,
    vin             CHAR(17) NOT NULL REFERENCES vehicle(vin) ON DELETE CASCADE,
    alert_type      VARCHAR(40) NOT NULL,          -- e.g. PREDICTED_FAILURE, DTC_RAISED
    severity        VARCHAR(10) NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    risk_score      NUMERIC(5,4),                  -- ML model output, 0..1
    details         JSONB NOT NULL DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','acknowledged','resolved')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by VARCHAR(100)
);
CREATE INDEX idx_alert_vin ON alert(vin);
-- Partial index: dashboards almost always filter to open alerts -> keep it small & fast
CREATE INDEX idx_alert_open ON alert(created_at DESC) WHERE status = 'open';

CREATE TABLE subscription (
    subscription_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fleet_id        UUID NOT NULL REFERENCES fleet(fleet_id) ON DELETE CASCADE,
    plan            VARCHAR(30) NOT NULL CHECK (plan IN ('starter','pro','enterprise')),
    seats           INTEGER NOT NULL DEFAULT 1,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    renews_at       TIMESTAMPTZ NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','past_due','canceled'))
);
CREATE INDEX idx_subscription_fleet ON subscription(fleet_id);

-- RBAC: users belong to a fleet (tenant isolation) and have a role
CREATE TABLE app_user (
    user_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fleet_id        UUID NOT NULL REFERENCES fleet(fleet_id) ON DELETE CASCADE,
    email           VARCHAR(200) NOT NULL UNIQUE,
    password_hash   VARCHAR(200) NOT NULL,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('admin','fleet_manager','viewer')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit log: every data access / AI-agent action, per NFR compliance requirement
CREATE TABLE audit_log (
    audit_id        BIGSERIAL PRIMARY KEY,
    actor_id        UUID,                 -- app_user.user_id, or NULL for system/agent
    actor_type      VARCHAR(20) NOT NULL CHECK (actor_type IN ('user','agent','system')),
    action          VARCHAR(100) NOT NULL,
    resource        VARCHAR(200),
    metadata        JSONB NOT NULL DEFAULT '{}',
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_occurred ON audit_log(occurred_at DESC);
