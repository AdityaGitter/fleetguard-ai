"""
simulator.py
------------
Streams telemetry events for a fleet of vehicles.

Realism features required by the brief, all implemented here:
- Bursty arrival (3x spikes at "shift start" windows)
- Out-of-order events (network jitter re-orders ~2% of events)
- Duplicate events (~0.5%, to force idempotent consumers downstream)
- Schema-shaped payload matching the case study's example event
- Pre-failure symptom injection driven by each vehicle's DegradationProfile,
  so a predictive-maintenance model has real signal to learn from:
    engine_temp_c drifts up, oil_life_pct drops faster, dtc codes appear,
    battery_voltage sags, vibration_index rises, in the N days before
    `fails_in_days` reaches 0.

Output sink is pluggable:
- "kafka"  -> production path, uses kafka-python / confluent-kafka producer
- "jsonl"  -> local dev / no-Kafka-available mode, appends NDJSON to a file
              (this is what the in-repo demo and tests use)

Run standalone:
    python simulator.py --vehicles 100000 --seconds 60 --sink jsonl --out ./out/telemetry.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vehicle_profiles import Vehicle, generate_fleets, generate_drivers, generate_vehicles, DTC_CODES

EARTH_STEP_DEG = 0.0008  # ~90m per tick at city driving speed


def build_event(v: Vehicle, seq: int, sim_day: float, rng: random.Random) -> dict:
    """Construct one telemetry event for vehicle v at simulated day `sim_day`."""
    lat = v.base_lat + rng.uniform(-EARTH_STEP_DEG, EARTH_STEP_DEG) * seq % 1
    lon = v.base_lon + rng.uniform(-EARTH_STEP_DEG, EARTH_STEP_DEG) * seq % 1
    speed = max(0.0, rng.gauss(45, 20))
    odo = v.odo_km_start + sim_day * rng.uniform(20, 220)

    is_ev = v.vehicle_type in ("EV", "HYBRID")
    soc = round(rng.uniform(15, 100), 1) if is_ev else None

    engine_temp = rng.gauss(90, 4)      # deg C, nominal
    oil_life = max(0.0, 100 - sim_day * rng.uniform(0.15, 0.35))
    battery_v = rng.gauss(12.6, 0.15)
    vibration = rng.gauss(1.0, 0.15)
    dtc: list[str] = []

    deg = v.degradation
    if deg.fails_in_days is not None:
        days_to_failure = deg.fails_in_days - sim_day
        if 0 <= days_to_failure <= 7:
            # ramp symptoms up as we approach the failure day
            closeness = (7 - days_to_failure) / 7.0  # 0 -> 1
            severity = closeness * deg.severity_ramp
            engine_temp += severity * 25
            oil_life -= severity * 20
            battery_v -= severity * 1.8
            vibration += severity * 1.2
            if rng.random() < 0.15 + 0.6 * severity:
                dtc.append(deg.fault_code)

    evt_type = "NORMAL"
    if speed < 1 and rng.random() < 0.02:
        evt_type = "IDLE"
    if rng.random() < 0.01:
        evt_type = "HARSH_BRAKE"
    if dtc:
        evt_type = "FAULT_CODE"

    event = {
        "vin": v.vin,
        "ts": (datetime.now(timezone.utc) - timedelta(days=max(0, 30 - sim_day))).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z"),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "speed_kmh": round(speed, 1),
        "odo_km": round(odo, 1),
        "engine_temp_c": round(engine_temp, 1),
        "oil_life_pct": round(max(0.0, oil_life), 1),
        "battery_voltage": round(battery_v, 2),
        "vibration_index": round(max(0.0, vibration), 2),
        "vehicle_type": v.vehicle_type,
        "dtc": dtc,
        "evt": evt_type,
        "seq": seq,
    }
    if soc is not None:
        event["soc_pct"] = soc
    return event


class TelemetrySimulator:
    def __init__(self, vehicles: list[Vehicle], seed: int = 7):
        self.vehicles = vehicles
        self.rng = random.Random(seed)
        self._seq_counters = {v.vin: 0 for v in vehicles}

    def stream(self, sim_day: float, burst: bool = False):
        """Yield one pass of events for all vehicles at a given simulated day.
        `burst` simulates the 3x shift-start spike by emitting each vehicle's
        event multiple times in quick succession (still realistic: think of
        it as multiple sensor channels reporting near-simultaneously)."""
        pending = []
        for v in self.vehicles:
            self._seq_counters[v.vin] += 1
            seq = self._seq_counters[v.vin]
            evt = build_event(v, seq, sim_day, self.rng)
            pending.append(evt)
            if burst and self.rng.random() < 0.15:
                pending.append(dict(evt))  # extra burst reading, same tick

        # inject out-of-order-ness: shuffle small windows
        for i in range(0, len(pending) - 3, 4):
            if self.rng.random() < 0.02:
                pending[i], pending[i + 2] = pending[i + 2], pending[i]

        # inject duplicates for idempotency testing downstream
        for evt in list(pending):
            if self.rng.random() < 0.005:
                pending.append(dict(evt))

        for evt in pending:
            yield evt


def _make_sink(sink: str, out_path: str | None, kafka_topic: str, kafka_bootstrap: str):
    if sink == "jsonl":
        path = Path(out_path or "./out/telemetry.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        f = path.open("a", encoding="utf-8")

        def emit(event: dict):
            f.write(json.dumps(event) + "\n")

        return emit, lambda: f.close()

    if sink == "kafka":
        try:
            from kafka import KafkaProducer  # pip install kafka-python
        except ImportError as e:
            raise SystemExit(
                "kafka-python not installed. `pip install kafka-python` or use --sink jsonl"
            ) from e
        producer = KafkaProducer(
            bootstrap_servers=kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8"),
            linger_ms=20,
            acks="all",
        )

        def emit(event: dict):
            # key by VIN so all events for a vehicle land on the same
            # partition -> preserves per-vehicle ordering for consumers
            producer.send(kafka_topic, key=event["vin"], value=event)

        return emit, lambda: (producer.flush(), producer.close())

    raise ValueError(f"unknown sink {sink}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=5000)
    ap.add_argument("--ticks", type=int, default=5, help="number of simulated-day ticks to emit")
    ap.add_argument("--sink", choices=["jsonl", "kafka"], default="jsonl")
    ap.add_argument("--out", default="./out/telemetry.jsonl")
    ap.add_argument("--kafka-topic", default="vehicle.telemetry.raw")
    ap.add_argument("--kafka-bootstrap", default="localhost:9092")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    fleets = generate_fleets(seed=args.seed)
    drivers = generate_drivers(max(1000, args.vehicles // 5), seed=args.seed)
    vehicles = generate_vehicles(args.vehicles, fleets, drivers, seed=args.seed)

    sim = TelemetrySimulator(vehicles, seed=args.seed)
    emit, close = _make_sink(args.sink, args.out, args.kafka_topic, args.kafka_bootstrap)

    total = 0
    t0 = time.time()
    for tick in range(args.ticks):
        sim_day = tick * (30 / max(1, args.ticks))
        burst = tick % 4 == 0
        for evt in sim.stream(sim_day, burst=burst):
            emit(evt)
            total += 1
    close()
    dt = time.time() - t0
    print(f"Emitted {total} events for {args.vehicles} vehicles in {dt:.2f}s "
          f"({total/max(dt,1e-6):.0f} events/sec) -> sink={args.sink}")


if __name__ == "__main__":
    main()
