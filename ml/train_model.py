"""
train_model.py
---------------
End-to-end training script for the predictive-maintenance model:

    "Which vehicles are likely to break down in the next 7 days?"

1. Generates a synthetic fleet with injected failure trajectories
2. Streams 30 simulated days of telemetry through the feature pipeline
3. Labels each (vin, day) row using the simulator's known ground truth
4. Trains a GradientBoostingClassifier and compares it against a baseline
5. Reports precision/recall/ROC-AUC and saves the model + feature list

Run:
    python train_model.py --vehicles 8000 --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulator"))
from vehicle_profiles import generate_fleets, generate_drivers, generate_vehicles  # noqa: E402
from simulator import TelemetrySimulator, build_event  # noqa: E402

from feature_engineering import FEATURE_COLUMNS, build_daily_features  # noqa: E402


def generate_labeled_dataset(n_vehicles: int, n_days: int, failure_rate: float, seed: int):
    fleets = generate_fleets(seed=seed)
    drivers = generate_drivers(max(1000, n_vehicles // 5), seed=seed)
    vehicles = generate_vehicles(n_vehicles, fleets, drivers, failure_rate=failure_rate, seed=seed)
    vehicles_by_vin = {v.vin: v for v in vehicles}

    rows = []
    for day in range(n_days):
        for v in vehicles:
            # a handful of events per vehicle per simulated day (not full IoT
            # cadence, to keep training fast; the daily aggregation pipeline
            # is the same one that would run over the real per-second stream)
            for tick in range(4):
                evt = build_event(v, seq=day * 4 + tick, sim_day=float(day),
                                   rng=__import__("random").Random(seed + day * 7 + tick))
                evt["_sim_day"] = day
                rows.append(evt)

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    df["dtc_count"] = df["dtc"].apply(len)
    df["harsh_brake"] = (df["evt"] == "HARSH_BRAKE").astype(int)
    df["day"] = df["_sim_day"]

    daily = (
        df.groupby(["vin", "day"])
        .agg(
            avg_speed_kmh=("speed_kmh", "mean"),
            max_engine_temp_c=("engine_temp_c", "max"),
            min_oil_life_pct=("oil_life_pct", "min"),
            avg_battery_voltage=("battery_voltage", "mean"),
            max_vibration_index=("vibration_index", "max"),
            dtc_event_count=("dtc_count", "sum"),
            harsh_brake_ct=("harsh_brake", "sum"),
            odo_km=("odo_km", "max"),
        )
        .reset_index()
        .sort_values(["vin", "day"])
    )

    for col in ["max_engine_temp_c", "min_oil_life_pct", "avg_battery_voltage", "max_vibration_index"]:
        daily[f"{col}_roll3"] = daily.groupby("vin")[col].transform(
            lambda s: s.rolling(3, min_periods=1).mean()
        )

    def label(row):
        v = vehicles_by_vin[row["vin"]]
        if v.degradation.fails_in_days is None:
            return 0
        days_until_failure = v.degradation.fails_in_days - row["day"]
        return int(0 <= days_until_failure <= 7)

    daily["label_fail_7d"] = daily.apply(label, axis=1)
    return daily, vehicles_by_vin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vehicles", type=int, default=8000)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--failure-rate", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "artifacts"))
    args = ap.parse_args()

    print(f"Generating {args.vehicles} vehicles x {args.days} days of telemetry ...")
    daily, vehicles_by_vin = generate_labeled_dataset(
        args.vehicles, args.days, args.failure_rate, args.seed
    )
    print(f"Feature table: {daily.shape[0]} rows, positive rate = "
          f"{daily['label_fail_7d'].mean():.3%}")

    feature_cols = FEATURE_COLUMNS + [
        "max_engine_temp_c_roll3", "min_oil_life_pct_roll3",
        "avg_battery_voltage_roll3", "max_vibration_index_roll3",
    ]
    X = daily[feature_cols].fillna(0)
    y = daily["label_fail_7d"]

    # split by vehicle (not row) to avoid leaking the same VIN's future days
    # into train/test on both sides
    vins = daily["vin"].unique()
    train_vins, test_vins = train_test_split(vins, test_size=0.25, random_state=args.seed)
    train_mask = daily["vin"].isin(train_vins)

    X_train, X_test = X[train_mask], X[~train_mask]
    y_train, y_test = y[train_mask], y[~train_mask]

    baseline = DummyClassifier(strategy="most_frequent").fit(X_train, y_train)
    logreg = LogisticRegression(max_iter=500, class_weight="balanced").fit(X_train, y_train)
    model = GradientBoostingClassifier(random_state=args.seed).fit(X_train, y_train)

    print("\n=== Baseline (majority class) ===")
    print(classification_report(y_test, baseline.predict(X_test), zero_division=0))

    print("=== Logistic Regression ===")
    print(classification_report(y_test, logreg.predict(X_test), zero_division=0))
    print("ROC-AUC:", round(roc_auc_score(y_test, logreg.predict_proba(X_test)[:, 1]), 4))

    print("\n=== Gradient Boosting (final model) ===")
    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    print(classification_report(y_test, preds, zero_division=0))
    auc = roc_auc_score(y_test, proba)
    print("ROC-AUC:", round(auc, 4))

    importances = sorted(
        zip(feature_cols, model.feature_importances_), key=lambda x: -x[1]
    )
    print("\nTop features:")
    for name, imp in importances[:5]:
        print(f"  {name:30s} {imp:.3f}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    import pickle
    with open(out_dir / "model.pkl", "wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols}, f)
    with open(out_dir / "metrics.json", "w") as f:
        json.dump({
            "roc_auc": auc,
            "n_train_rows": int(len(X_train)),
            "n_test_rows": int(len(X_test)),
            "positive_rate": float(y.mean()),
            "top_features": [{"name": n, "importance": float(i)} for n, i in importances],
        }, f, indent=2)
    print(f"\nSaved model + metrics to {out_dir}/")


if __name__ == "__main__":
    main()
