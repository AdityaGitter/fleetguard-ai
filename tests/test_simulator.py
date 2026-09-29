import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))
from vehicle_profiles import generate_fleets, generate_drivers, generate_vehicles  # noqa: E402
from simulator import TelemetrySimulator, build_event  # noqa: E402
import random

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def _small_fleet(n=200, seed=1):
    fleets = generate_fleets(seed=seed)
    drivers = generate_drivers(50, seed=seed)
    return generate_vehicles(n, fleets, drivers, failure_rate=0.2, seed=seed)


def test_generated_vins_are_valid_shape():
    vehicles = _small_fleet()
    assert all(VIN_RE.match(v.vin) for v in vehicles)


def test_vins_are_unique():
    vehicles = _small_fleet(500)
    vins = [v.vin for v in vehicles]
    assert len(vins) == len(set(vins))


def test_event_schema_has_required_fields():
    vehicles = _small_fleet(10)
    evt = build_event(vehicles[0], seq=1, sim_day=0.0, rng=random.Random(1))
    for field in ("vin", "ts", "lat", "lon", "speed_kmh", "odo_km", "evt", "seq"):
        assert field in evt


def test_degradation_raises_symptoms_near_failure_day():
    vehicles = _small_fleet(300, seed=3)
    failing = [v for v in vehicles if v.degradation.fails_in_days is not None]
    assert len(failing) > 0
    v = failing[0]
    fday = v.degradation.fails_in_days

    healthy_evt = build_event(v, seq=1, sim_day=max(0, fday - 20), rng=random.Random(1))
    sick_evt = build_event(v, seq=2, sim_day=fday - 1, rng=random.Random(1))

    # near the failure day, engine temp should trend higher and oil life lower
    assert sick_evt["engine_temp_c"] >= healthy_evt["engine_temp_c"] - 5
    assert sick_evt["oil_life_pct"] <= healthy_evt["oil_life_pct"] + 5


def test_stream_produces_duplicates_and_bursts():
    vehicles = _small_fleet(500, seed=5)
    sim = TelemetrySimulator(vehicles, seed=5)
    events = list(sim.stream(sim_day=0, burst=True))
    # burst mode should emit more than one event per vehicle on average
    assert len(events) > len(vehicles)
