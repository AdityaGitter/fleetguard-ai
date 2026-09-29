"""
seed_all.py
-----------
One-command local bootstrap:
  1. Generates 100k vehicles/drivers/fleets
  2. Inserts master data into Postgres (vehicle, driver, fleet, app_user)
  3. Creates the demo login user with a real bcrypt hash
  4. Trains the predictive-maintenance model (writes ml/artifacts/model.pkl)

Run (with the docker-compose stack already up):
    python scripts/seed_all.py --vehicles 100000
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import psycopg2
from psycopg2.extras import execute_values

from vehicle_profiles import generate_fleets, generate_drivers, generate_vehicles
from auth import hash_password

DEMO_FLEET_ID = "11111111-1111-1111-1111-111111111111"


def seed_postgres(dsn: str, n_vehicles: int, seed: int):
    fleets = generate_fleets(seed=seed)
    drivers = generate_drivers(max(1000, n_vehicles // 5), seed=seed)
    vehicles = generate_vehicles(n_vehicles, fleets, drivers, seed=seed)

    # Force the first N vehicles onto the demo fleet so the dashboard has
    # something to show immediately after `docker compose up`.
    for v in vehicles[:200]:
        v.fleet_id = DEMO_FLEET_ID

    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO fleet (fleet_id, name, segment) VALUES (%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (DEMO_FLEET_ID, "Demo Fleet Co", "commercial"),
        )
        execute_values(cur,
            "INSERT INTO fleet (fleet_id, name, segment) VALUES %s ON CONFLICT DO NOTHING",
            [(f.fleet_id, f.name, f.segment) for f in fleets])
        execute_values(cur,
            "INSERT INTO driver (driver_id, fleet_id, name, license_no) VALUES %s "
            "ON CONFLICT DO NOTHING",
            [(d.driver_id, fleets[0].fleet_id, d.name, d.license_no) for d in drivers])
        execute_values(cur,
            "INSERT INTO vehicle (vin, fleet_id, driver_id, vehicle_type, make, model, "
            "model_year, odo_km_baseline) VALUES %s ON CONFLICT DO NOTHING",
            [(v.vin, v.fleet_id, v.driver_id, v.vehicle_type, v.make, v.model,
              v.year, v.odo_km_start) for v in vehicles])

        pw_hash = hash_password("demo1234")
        cur.execute(
            "INSERT INTO app_user (fleet_id, email, password_hash, role) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash",
            (DEMO_FLEET_ID, "manager@fleet.demo", pw_hash, "fleet_manager"),
        )
    conn.commit()
    conn.close()
    print(f"Seeded {len(fleets)} fleets, {len(drivers)} drivers, {len(vehicles)} vehicles. "
          f"Demo login: manager@fleet.demo / demo1234")


def train_model():
    print("Training predictive-maintenance model ...")
    subprocess.run([sys.executable, "train_model.py", "--vehicles", "5000", "--days", "30"],
                    cwd=str(Path(__file__).resolve().parent.parent / "ml"), check=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dsn", default="postgresql://cvuser:cvpass@localhost:5432/cv_platform")
    ap.add_argument("--skip-train", action="store_true")
    args = ap.parse_args()

    seed_postgres(args.dsn, args.vehicles, args.seed)
    if not args.skip_train:
        train_model()
