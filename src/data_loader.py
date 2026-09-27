"""Load the raw BTC options trade tape and normalize it into a fast, queryable cache.

Raw files: data/BTC_2026-*.csv with columns
    product_symbol, price, size, timestamp, buyer_role
where product_symbol = {C|P}-BTC-{strike}-{DDMMYY}.

We parse these once into a parquet dataset partitioned by expiry date so the
backtest can read all trades for a single expiry (across month boundaries) cheaply.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from config import DATA_DIR, TAPE_DATASET

_SYMBOL_RE = re.compile(r"^([CP])-BTC-(\d+)-(\d{6})$")
_CHUNK = 2_000_000


def _parse_symbols(sym: pd.Series):
    """Vectorized parse of product_symbol -> (opt_type, strike, expiry_date)."""
    extracted = sym.str.extract(_SYMBOL_RE)
    extracted.columns = ["opt_type", "strike", "ddmmyy"]
    strike = pd.to_numeric(extracted["strike"], errors="coerce")
    # DDMMYY -> date (all years are 20xx here)
    expiry = pd.to_datetime(extracted["ddmmyy"], format="%d%m%y", errors="coerce", utc=True)
    return extracted["opt_type"], strike, expiry


def build_tape_cache(force: bool = False) -> None:
    """Parse every monthly CSV into the partitioned parquet dataset (idempotent)."""
    if TAPE_DATASET.exists() and any(TAPE_DATASET.rglob("*.parquet")) and not force:
        return
    if TAPE_DATASET.exists():
        # rebuild cleanly
        import shutil
        shutil.rmtree(TAPE_DATASET)
    TAPE_DATASET.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(glob.glob(str(DATA_DIR / "BTC_2026-*.csv")))
    if not csv_files:
        raise FileNotFoundError(f"No BTC_2026-*.csv files found in {DATA_DIR}")

    file_idx = 0
    for csv in csv_files:
        month = Path(csv).stem
        reader = pd.read_csv(
            csv,
            usecols=["product_symbol", "price", "size", "timestamp", "buyer_role"],
            dtype={"product_symbol": "string", "buyer_role": "category"},
            chunksize=_CHUNK,
        )
        for chunk in reader:
            opt_type, strike, expiry = _parse_symbols(chunk["product_symbol"])
            df = pd.DataFrame({
                "opt_type": opt_type.astype("string"),
                "strike": strike,
                "expiry": expiry,
                "price": pd.to_numeric(chunk["price"], errors="coerce").astype("float64"),
                "size": pd.to_numeric(chunk["size"], errors="coerce").astype("float64"),
                "ts": pd.to_datetime(chunk["timestamp"], errors="coerce", utc=True,
                                     format="ISO8601"),
                "buyer_role": chunk["buyer_role"].astype("string"),
            })
            df = df.dropna(subset=["opt_type", "strike", "expiry", "price", "ts"])
            df["strike"] = df["strike"].astype("int64")
            df["expiry_key"] = df["expiry"].dt.strftime("%Y-%m-%d")
            if df.empty:
                continue
            table = pa.Table.from_pandas(df, preserve_index=False)
            pq.write_to_dataset(
                table,
                root_path=str(TAPE_DATASET),
                partition_cols=["expiry_key"],
                basename_template=f"{month}-part{file_idx}-{{i}}.parquet",
            )
            file_idx += 1


def list_expiries() -> list[pd.Timestamp]:
    """All expiry dates present in the cached tape, sorted ascending."""
    dataset = ds.dataset(str(TAPE_DATASET), format="parquet", partitioning="hive")
    keys = set()
    for frag in dataset.get_fragments():
        # partition expression like: (expiry_key == "2026-01-15")
        for part in str(frag.partition_expression).replace("(", "").replace(")", "").split(" and "):
            if "expiry_key" in part:
                val = part.split("==")[-1].strip().strip('"')
                keys.add(val)
    return sorted(pd.Timestamp(k, tz="UTC") for k in keys if k)


def load_expiry_trades(expiry: pd.Timestamp) -> pd.DataFrame:
    """All trades for a single expiry, sorted by timestamp."""
    key = pd.Timestamp(expiry).strftime("%Y-%m-%d")
    dataset = ds.dataset(str(TAPE_DATASET), format="parquet", partitioning="hive")
    tbl = dataset.to_table(filter=ds.field("expiry_key") == key)
    df = tbl.to_pandas()
    if df.empty:
        return df
    df["expiry"] = pd.Timestamp(expiry)
    return df.sort_values("ts").reset_index(drop=True)
