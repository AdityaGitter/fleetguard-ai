"""
feature_engineering.py
-----------------------
Turns a stream of raw telemetry events into per-vehicle, per-day feature
rows suitable for the predictive-maintenance classifier.

In production this logic runs as a Kafka Streams / Flink job maintaining
rolling windows per VIN and writing feature rows to a feature store; here
it's expressed as a pandas pipeline over a batch of simulated events so it
can be trained, tested and demoed without a live cluster.
"""

from __future__ import annotations

import pandas as pd


FEATURE_COLUMNS = [
    "avg_speed_kmh", "max_engine_temp_c", "min_oil_life_pct",
    "avg_battery_voltage", "max_vibration_index", "dtc_event_count",
    "harsh_brake_ct", "odo_km",
]


def events_to_dataframe(events: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(events)
    df["ts"] = pd.to_datetime(df["ts"])
    df["dtc_count"] = df["dtc"].apply(lambda x: len(x) if isinstance(x, list) else 0)
    df["harsh_brake"] = (df["evt"] == "HARSH_BRAKE").astype(int)
    df["day"] = df["ts"].dt.floor("D")
    return df


def build_daily_features(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate raw events into one row per (vin, day) with rolling-window
    features. This is what a stream processor would emit continuously;
    here we compute it in one batch pass over historical simulator output."""
    grouped = (
        df.sort_values("ts")
        .groupby(["vin", "day"])
        .agg(
            avg_speed_kmh=("speed_kmh", "mean"),
            max_engine_temp_c=("engine_temp_c", "max"),
            min_oil_life_pct=("oil_life_pct", "min"),
            avg_battery_voltage=("battery_voltage", "mean"),
            max_vibration_index=("vibration_index", "max"),
            dtc_event_count=("dtc_count", "sum"),
            harsh_brake_ct=("harsh_brake", "sum"),
            odo_km=("odo_km", "max"),
            vehicle_type=("vehicle_type", "first"),
        )
        .reset_index()
    )

    # 3-day rolling trend features (captures "is this getting worse")
    grouped = grouped.sort_values(["vin", "day"])
    for col in ["max_engine_temp_c", "min_oil_life_pct", "avg_battery_voltage",
                "max_vibration_index"]:
        grouped[f"{col}_roll3"] = (
            grouped.groupby("vin")[col].transform(lambda s: s.rolling(3, min_periods=1).mean())
        )

    return grouped


def label_failure_within_7_days(features: pd.DataFrame, vehicles_by_vin: dict) -> pd.DataFrame:
    """Attach ground-truth label using the simulator's hidden degradation
    profile: 1 if the vehicle's injected failure day falls within the next
    7 days of this feature row, else 0. This mirrors how you'd backfill
    labels from real work-order / breakdown records in production."""
    def label_row(row):
        v = vehicles_by_vin.get(row["vin"])
        if v is None or v.degradation.fails_in_days is None:
            return 0
        # `day` is a pandas Timestamp representing simulated elapsed days;
        # the simulator encodes sim_day in the event day-of-month offset,
        # so we recover the same 0..30 scale used at generation time.
        sim_day_index = row.get("_sim_day", None)
        if sim_day_index is None:
            return 0
        days_until_failure = v.degradation.fails_in_days - sim_day_index
        return int(0 <= days_until_failure <= 7)

    features = features.copy()
    features["label_fail_7d"] = features.apply(label_row, axis=1)
    return features
