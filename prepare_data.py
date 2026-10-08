"""Run ONCE on your computer: shrink the big BTS CSV into two fast Parquet files.

    python prepare_data.py data/raw/YOUR_FILE.csv

Creates:
  data/flights.parquet         all rows, for your notebook analysis (do NOT commit)
  data/flights_sample.parquet  10% random sample, small enough for GitHub + Streamlit Cloud
"""
import sys
from pathlib import Path

import pandas as pd

KEEP = [
    "fl_date", "month", "day_of_week", "op_unique_carrier", "origin", "dest",
    "crs_dep_time", "dep_delay", "taxi_out", "taxi_in", "arr_delay",
    "cancelled", "cancellation_code", "diverted", "crs_elapsed_time",
    "actual_elapsed_time", "distance",
    "carrier_delay", "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay",
]
TEXT_COLUMNS = ["op_unique_carrier", "origin", "dest", "cancellation_code"]
SAMPLE_FRACTION = 0.10


def main(raw_path: str) -> None:
    # Peek at the header so column-name capitalisation doesn't matter.
    header = pd.read_csv(raw_path, nrows=0).columns
    actual = {c.strip().lower(): c for c in header}
    missing = [c for c in KEEP if c not in actual]
    if missing:
        print(f"Warning: these columns weren't found and will be skipped: {missing}")
    usecols = [actual[c] for c in KEEP if c in actual]
    dtypes = {actual[c]: "category" for c in TEXT_COLUMNS if c in actual}

    print("Reading the CSV (a few minutes for 7M rows)...")
    df = pd.read_csv(raw_path, usecols=usecols, dtype=dtypes, low_memory=False)
    df.columns = df.columns.str.strip().str.lower()

    # Shrink numbers from 64-bit to 32-bit: same values, half the memory.
    for col in df.select_dtypes("number").columns:
        df[col] = pd.to_numeric(df[col], downcast="float")
    df["fl_date"] = pd.to_datetime(df["fl_date"], errors="coerce")

    mb = df.memory_usage(deep=True).sum() / 1e6
    print(f"Loaded {len(df):,} rows x {df.shape[1]} columns using {mb:,.0f} MB of memory")

    out = Path("data")
    out.mkdir(exist_ok=True)
    df.to_parquet(out / "flights.parquet", index=False)
    df.sample(frac=SAMPLE_FRACTION, random_state=42).to_parquet(out / "flights_sample.parquet", index=False)
    for f in ("flights.parquet", "flights_sample.parquet"):
        print(f"Saved data/{f}  ({(out / f).stat().st_size / 1e6:,.0f} MB on disk)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python prepare_data.py data/raw/YOUR_FILE.csv")
    main(sys.argv[1])
