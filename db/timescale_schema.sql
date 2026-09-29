-- ============================================================================
-- TimescaleDB hypertable for raw + down-sampled telemetry.
-- This is the AP / eventually-consistent side of the store: a dropped or
-- delayed telemetry point is not business-critical the way a billing record
-- is, so we trade strict consistency for ingest throughput (PACELC: under
-- partition we choose A; even without partition we favor low latency (L)
-- over immediate cross-replica consistency (C)).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE telemetry (
    vin             CHAR(17) NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    speed_kmh       REAL,
    odo_km          REAL,
    engine_temp_c   REAL,
    oil_life_pct    REAL,
    battery_voltage REAL,
    soc_pct         REAL,
    vibration_index REAL,
    evt             VARCHAR(20),
    dtc             TEXT[],          -- fault codes present on this event, if any
    seq             BIGINT,          -- per-vehicle monotonic sequence, for de-dup
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hypertable partitioned by time; space-partitioned by vin hash to spread
-- writes across chunks and avoid a single hot chunk during shift-start bursts.
SELECT create_hypertable(
    'telemetry', 'ts',
    partitioning_column => 'vin',
    number_partitions => 16,
    chunk_time_interval => INTERVAL '1 day'
);

-- Composite index for the API's most common query: "give me vehicle X's
-- telemetry between time A and B" (keyset-pagination friendly).
CREATE INDEX idx_telemetry_vin_ts ON telemetry (vin, ts DESC);

-- De-dup guard: the simulator/consumer intentionally produces ~0.5% duplicate
-- events to exercise idempotent ingestion; the consumer upserts on this key.
CREATE UNIQUE INDEX uq_telemetry_vin_seq ON telemetry (vin, seq, ts);

-- Compression + retention policy: hot (7 days uncompressed) -> warm (91 days
-- compressed) -> cold (drop raw, keep 5-min continuous aggregate for 2 years).
ALTER TABLE telemetry SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'vin'
);
SELECT add_compression_policy('telemetry', INTERVAL '7 days');
SELECT add_retention_policy('telemetry', INTERVAL '91 days');

-- Continuous aggregate: 5-minute rollups per vehicle, kept for 2 years for
-- historical/batch analytics and model retraining without scanning raw rows.
CREATE MATERIALIZED VIEW telemetry_5min
WITH (timescaledb.continuous) AS
SELECT
    vin,
    time_bucket('5 minutes', ts) AS bucket,
    avg(speed_kmh)        AS avg_speed_kmh,
    max(engine_temp_c)    AS max_engine_temp_c,
    min(oil_life_pct)     AS min_oil_life_pct,
    avg(battery_voltage)  AS avg_battery_voltage,
    max(vibration_index)  AS max_vibration_index,
    count(*) FILTER (WHERE array_length(dtc, 1) > 0) AS dtc_event_count,
    count(*)              AS sample_count
FROM telemetry
GROUP BY vin, bucket;

SELECT add_continuous_aggregate_policy('telemetry_5min',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes'
);

-- ============================================================================
-- MongoDB (documented here for reference; provisioned via docker-compose,
-- schema is intentionally flexible/document-shaped):
--
--   db.raw_events        -- append-only landing zone for every ingested event
--                            exactly as received (pre-validation), for replay
--                            and audit. Schema varies as OEM formats change,
--                            which is exactly why this is NOT relational.
--   db.oem_field_mappings -- per-OEM-brand field-mapping documents used by the
--                            normalisation engine (nested/variable structure).
--   db.diagnostic_reports -- unstructured technician notes / service records
--                            attached to a VIN, free-text + attachments.
--
-- Vector store (pgvector, colocated with Postgres for the MVP):
--   embedding table for the agentic-AI layer (Section "Agentic AI"): stores
--   embeddings of alerts/service notes so the fleet-copilot agent can do
--   semantic retrieval ("show me vehicles with issues like this one").
-- ============================================================================
