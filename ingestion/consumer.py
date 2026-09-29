"""
consumer.py
-----------
Kafka consumer for the `vehicle.telemetry.raw` topic.

Responsibilities (matching the "IoT ingestion" row of the technical
expectations table):
  - Idempotent writes: upsert on (vin, seq, ts) so replays/duplicates from
    the simulator (or real OEM retries) never double-count.
  - Schema validation before anything is persisted; invalid events go to a
    dead-letter topic instead of silently corrupting the store.
  - Back-pressure aware: commits offsets only after a batch is durably
    written, and the consumer group can be scaled out by adding partitions
    + more consumer instances with no code change.
  - Dual write: raw JSON -> MongoDB (audit/replay), structured -> TimescaleDB
    (queryable time-series). A production system would instead use a single
    write (e.g. to Kafka-backed CDC or an outbox) to avoid dual-write
    inconsistency risk; documented as a known trade-off in docs/ARCHITECTURE.md.

Run:
    python consumer.py --bootstrap localhost:9092 --topic vehicle.telemetry.raw
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
DTC_RE = re.compile(r"^[PBCU]\d{4}$")

REQUIRED_FIELDS = {"vin", "ts", "lat", "lon", "speed_kmh", "seq"}


def validate_event(evt: dict) -> tuple[bool, str | None]:
    if not REQUIRED_FIELDS.issubset(evt.keys()):
        return False, f"missing fields: {REQUIRED_FIELDS - evt.keys()}"
    if not VIN_RE.match(evt["vin"]):
        return False, "invalid VIN format"
    for code in evt.get("dtc", []):
        if not DTC_RE.match(code):
            return False, f"invalid DTC code {code}"
    try:
        datetime.fromisoformat(evt["ts"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False, "invalid timestamp"
    if not (-90 <= evt["lat"] <= 90 and -180 <= evt["lon"] <= 180):
        return False, "lat/lon out of range"
    return True, None


class TelemetryConsumer:
    """Wraps the batching / dual-write logic so it's testable without a
    live Kafka broker (see tests/test_ingestion.py, which feeds this class
    events directly instead of via a real consumer poll loop)."""

    def __init__(self, pg_conn=None, mongo_col=None, batch_size: int = 500):
        self.pg_conn = pg_conn
        self.mongo_col = mongo_col
        self.batch_size = batch_size
        self._batch: list[dict] = []
        self.dead_letters: list[dict] = []
        self.processed_count = 0
        self.duplicate_count = 0
        self._seen: set[tuple] = set()  # (vin, seq) idempotency guard, demo-scale only;
        # production uses the DB's own UNIQUE (vin, seq, ts) constraint via upsert.

    def handle(self, evt: dict):
        ok, err = validate_event(evt)
        if not ok:
            self.dead_letters.append({**evt, "_error": err})
            return

        key = (evt["vin"], evt["seq"])
        if key in self._seen:
            self.duplicate_count += 1
            return
        self._seen.add(key)

        self._batch.append(evt)
        if len(self._batch) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self._batch:
            return
        if self.mongo_col is not None:
            self.mongo_col.insert_many(self._batch, ordered=False)
        if self.pg_conn is not None:
            self._upsert_timescale(self._batch)
        self.processed_count += len(self._batch)
        self._batch.clear()

    def _upsert_timescale(self, batch: list[dict]):
        rows = [
            (
                e["vin"], e["ts"], e["lat"], e["lon"], e["speed_kmh"], e.get("odo_km"),
                e.get("engine_temp_c"), e.get("oil_life_pct"), e.get("battery_voltage"),
                e.get("soc_pct"), e.get("vibration_index"), e.get("evt"), e.get("dtc", []),
                e["seq"],
            )
            for e in batch
        ]
        with self.pg_conn.cursor() as cur:
            from psycopg2.extras import execute_values
            execute_values(cur, """
                INSERT INTO telemetry (vin, ts, lat, lon, speed_kmh, odo_km, engine_temp_c,
                    oil_life_pct, battery_voltage, soc_pct, vibration_index, evt, dtc, seq)
                VALUES %s
                ON CONFLICT (vin, seq, ts) DO NOTHING
            """, rows)
        self.pg_conn.commit()


def run_kafka_loop(bootstrap: str, topic: str, dlq_topic: str):
    from kafka import KafkaConsumer, KafkaProducer
    import psycopg2
    import pymongo
    import os

    pg_conn = psycopg2.connect(os.environ.get("DATABASE_URL",
        "postgresql://cvuser:cvpass@localhost:5432/cv_platform"))
    mongo_col = pymongo.MongoClient(
        os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    )["cv_platform"]["raw_events"]
    dlq_producer = KafkaProducer(bootstrap_servers=bootstrap,
                                  value_serializer=lambda v: json.dumps(v).encode())

    consumer = KafkaConsumer(
        topic, bootstrap_servers=bootstrap, group_id="telemetry-ingestion",
        value_deserializer=lambda v: json.loads(v.decode()),
        enable_auto_commit=False, max_poll_records=500,
    )
    handler = TelemetryConsumer(pg_conn=pg_conn, mongo_col=mongo_col)

    print(f"Consuming {topic} from {bootstrap} ...")
    for msg in consumer:
        handler.handle(msg.value)

        if handler.dead_letters:
            for dl in handler.dead_letters:
                dlq_producer.send(dlq_topic, dl)
            handler.dead_letters.clear()

    # Flush any remaining events so small batches are persisted.
        handler.flush()

    # Commit only after the data has been durably written.
        consumer.commit()
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--topic", default="vehicle.telemetry.raw")
    ap.add_argument("--dlq-topic", default="vehicle.telemetry.deadletter")
    args = ap.parse_args()
    run_kafka_loop(args.bootstrap, args.topic, args.dlq_topic)
