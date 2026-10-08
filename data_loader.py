"""Load flight data (Parquet or CSV) and add the metrics the analysis needs."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# DOT definition: a flight is on time if it arrives less than 15 minutes late.
SLA_THRESHOLD_MIN = 15

DELAY_CAUSES = ["carrier_delay", "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay"]
# Late aircraft = delay inherited from the plane's previous flight. The other four are
# where delay is actually *born*, so we call them "originating" causes.
ORIGINATING = ["carrier_delay", "weather_delay", "nas_delay", "security_delay"]

CAUSE_LABELS = {
    "carrier_delay": "Airline issues",
    "weather_delay": "Weather",
    "nas_delay": "Air traffic control",
    "security_delay": "Security",
    "late_aircraft_delay": "Late aircraft",
}

COLUMN_ALIASES = {"op_carrier": "op_unique_carrier", "reporting_airline": "op_unique_carrier",
                  "flightdate": "fl_date"}
REQUIRED = ["fl_date", "op_unique_carrier", "origin", "dest", "arr_delay"]
NUMERIC = ["arr_delay", "dep_delay", "taxi_out", "taxi_in", "crs_dep_time", "distance"]
WANTED = set(REQUIRED + NUMERIC + DELAY_CAUSES + list(COLUMN_ALIASES)) | {
    "cancelled", "cancellation_code", "diverted"}


def load_flights(source) -> pd.DataFrame:
    """Accepts a .parquet path, a .csv path, or an uploaded CSV file."""
    if isinstance(source, (str, Path)) and str(source).endswith(".parquet"):
        df = pd.read_parquet(source)
    else:
        df = pd.read_csv(source, low_memory=False, usecols=lambda c: c.strip().lower() in WANTED)
    return prepare(df)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = df.columns.str.strip().str.lower()
    for old, new in COLUMN_ALIASES.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Dataset is missing required columns: {missing}")

    for col in ("op_unique_carrier", "origin", "dest"):
        df[col] = df[col].astype("category")

    # BTS only records causes for flights 15+ min late. Blank = no attributed delay = 0.
    for col in DELAY_CAUSES:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("float32")

    for col in ("cancelled", "diverted"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int) if col in df.columns else 0

    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce") if col in df.columns else float("nan")

    df["fl_date"] = pd.to_datetime(df["fl_date"], errors="coerce").dt.normalize()

    # --- Engineered metrics ---
    df["completed"] = (df["cancelled"] == 0) & (df["diverted"] == 0) & df["arr_delay"].notna()
    df["sla_breach"] = df["completed"] & (df["arr_delay"] >= SLA_THRESHOLD_MIN)
    df["originating_delay"] = df[ORIGINATING].sum(axis=1)
    df["cause_delay_total"] = df["originating_delay"] + df["late_aircraft_delay"]
    df["ground_time"] = df["taxi_out"] + df["taxi_in"]
    df["dep_hour"] = (df["crs_dep_time"] // 100 % 24).astype("Int64")
    return df
