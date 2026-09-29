"""
vehicle_profiles.py
--------------------
Generates the master data (Fleets, Vehicles, Drivers) that the telemetry
simulator streams events for, and that gets seeded into the relational
(Postgres/TimescaleDB) store.

Design notes:
- VINs are generated as valid-shaped 17-char VINs (no I/O/Q), matching the
  regex validation rule described in the case study.
- Each vehicle gets a "degradation profile": a hidden failure-risk trajectory
  that the telemetry generator uses to inject realistic pre-failure signal
  (rising engine temp, DTC codes, battery decay, etc.) in the days before a
  simulated breakdown. This is what makes the predictive-maintenance ML
  problem learnable instead of pure noise.
"""

import random
import string
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

VIN_ALPHABET = [c for c in string.ascii_uppercase + string.digits if c not in "IOQ"]

VEHICLE_TYPES = ["ICE", "HYBRID", "EV"]
VEHICLE_TYPE_WEIGHTS = [0.55, 0.20, 0.25]  # mixed fleet, EV-skewed toward future trend

MAKES_MODELS = {
    "ICE": [("Volvo", "S60"), ("Subaru", "Outback"), ("Stellantis", "Ram1500"), ("Ford", "F150")],
    "HYBRID": [("Toyota", "RAV4Hybrid"), ("Ford", "Escape"), ("Honda", "CRV")],
    "EV": [("Volvo", "EX30"), ("Stellantis", "Ram1500REV"), ("Tesla", "Model3"), ("Rivian", "R1T")],
}

FLEET_NAMES = [
    "Metro Rental Co", "Apex Logistics", "GreenMile Delivery", "CityLink Fleet",
    "Summit Freight", "Coastal Rentals", "Prime Movers", "Urban Transit Partners",
]

DTC_CODES = ["P0301", "P0171", "P0420", "P0128", "P0455", "C0035", "B1318", "U0100"]


def _gen_vin() -> str:
    return "".join(random.choices(VIN_ALPHABET, k=17))


@dataclass
class DegradationProfile:
    """Hidden ground-truth state used only by the simulator/label generator.

    fails_in_days: None if the vehicle stays healthy for the whole simulation
                   window, else the number of days until a simulated
                   breakdown/service event.
    severity_ramp: how quickly symptoms escalate as failure approaches.
    """
    fails_in_days: int | None
    severity_ramp: float
    fault_code: str | None


@dataclass
class Vehicle:
    vin: str
    fleet_id: str
    driver_id: str
    vehicle_type: str
    make: str
    model: str
    year: int
    odo_km_start: float
    base_lat: float
    base_lon: float
    degradation: DegradationProfile = field(repr=False)


@dataclass
class Driver:
    driver_id: str
    name: str
    license_no: str
    safety_score_bias: float  # -1..1, negative = riskier driver baseline


@dataclass
class Fleet:
    fleet_id: str
    name: str
    segment: str  # commercial | rental | lending_collateral | dealership


# A handful of realistic depot centers (lat/lon) so GPS traces cluster sensibly
DEPOTS = [
    (13.0827, 80.2707),   # Chennai
    (12.9716, 77.5946),   # Bangalore
    (37.7749, -122.4194), # San Francisco
    (47.6062, -122.3321), # Seattle
    (28.6139, 77.2090),   # Delhi
]


def generate_fleets(n_fleets: int = 12, seed: int = 42) -> list[Fleet]:
    rng = random.Random(seed)
    segments = ["commercial", "rental", "lending_collateral", "dealership"]
    fleets = []
    for i in range(n_fleets):
        fleets.append(Fleet(
            fleet_id=str(uuid.uuid4()),
            name=f"{rng.choice(FLEET_NAMES)} #{i+1}",
            segment=rng.choice(segments),
        ))
    return fleets


def generate_drivers(n_drivers: int, seed: int = 42) -> list[Driver]:
    rng = random.Random(seed + 1)
    drivers = []
    for i in range(n_drivers):
        drivers.append(Driver(
            driver_id=str(uuid.uuid4()),
            name=f"Driver_{i:06d}",
            license_no="".join(rng.choices(string.ascii_uppercase + string.digits, k=10)),
            safety_score_bias=rng.uniform(-1, 1),
        ))
    return drivers


def generate_vehicles(
    n_vehicles: int,
    fleets: list[Fleet],
    drivers: list[Driver],
    failure_rate: float = 0.08,
    seed: int = 42,
) -> list[Vehicle]:
    """failure_rate: fraction of vehicles that will develop a simulated
    fault during the simulation window (used to generate learnable labels)."""
    rng = random.Random(seed + 2)
    vehicles = []
    for i in range(n_vehicles):
        vtype = rng.choices(VEHICLE_TYPES, weights=VEHICLE_TYPE_WEIGHTS, k=1)[0]
        make, model = rng.choice(MAKES_MODELS[vtype])
        depot = rng.choice(DEPOTS)

        will_fail = rng.random() < failure_rate
        degradation = DegradationProfile(
            fails_in_days=rng.randint(1, 14) if will_fail else None,
            severity_ramp=rng.uniform(0.6, 1.4),
            fault_code=rng.choice(DTC_CODES) if will_fail else None,
        )

        vehicles.append(Vehicle(
            vin=_gen_vin(),
            fleet_id=rng.choice(fleets).fleet_id,
            driver_id=rng.choice(drivers).driver_id,
            vehicle_type=vtype,
            make=make,
            model=model,
            year=rng.randint(2018, 2026),
            odo_km_start=rng.uniform(500, 180_000),
            base_lat=depot[0] + rng.uniform(-0.3, 0.3),
            base_lon=depot[1] + rng.uniform(-0.3, 0.3),
            degradation=degradation,
        ))
    return vehicles


if __name__ == "__main__":
    fleets = generate_fleets()
    drivers = generate_drivers(20_000)
    vehicles = generate_vehicles(100_000, fleets, drivers)
    n_at_risk = sum(1 for v in vehicles if v.degradation.fails_in_days is not None)
    print(f"Generated {len(fleets)} fleets, {len(drivers)} drivers, {len(vehicles)} vehicles")
    print(f"{n_at_risk} vehicles ({n_at_risk/len(vehicles):.1%}) carry an injected failure trajectory")
