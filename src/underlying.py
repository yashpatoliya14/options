"""Fetch and serve the BTC underlying index price from Delta Exchange.

The tape has no spot, so we pull the .DEXBTUSD index OHLC candles (public, no auth)
and expose:
    - spot_at(ts): nearest index price at an arbitrary time (for entry forward + marks)
    - settlement_price(expiry): index price at the option settlement time (12:00 UTC)

Data is cached to parquet so repeat runs are offline and reproducible.
"""
from __future__ import annotations

import time
import urllib.request
import json

import pandas as pd

from config import DELTA_BASE, INDEX_SYMBOL, INDEX_CACHE, SETTLEMENT_HOUR_UTC

_MAX_CANDLES = 1800  # stay under Delta's per-request cap


def _http_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "btc-condor-backtest"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _fetch_range(symbol: str, start: int, end: int, resolution: str = "1h") -> pd.DataFrame:
    """Fetch candles between unix seconds [start, end], paginating on the per-call cap."""
    step_seconds = {"1h": 3600, "1d": 86400}[resolution]
    window = _MAX_CANDLES * step_seconds
    rows = []
    cur = start
    while cur < end:
        chunk_end = min(cur + window, end)
        url = (f"{DELTA_BASE}/v2/history/candles?resolution={resolution}"
               f"&symbol={symbol}&start={cur}&end={chunk_end}")
        data = _http_get(url).get("result", [])
        rows.extend(data)
        cur = chunk_end
        time.sleep(0.2)  # be polite to the API
    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    df = pd.DataFrame(rows).drop_duplicates(subset="time").sort_values("time")
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.reset_index(drop=True)


class Underlying:
    def __init__(self, candles: pd.DataFrame):
        self._c = candles.set_index("dt").sort_index()

    @classmethod
    def load(cls, start: pd.Timestamp, end: pd.Timestamp, force: bool = False) -> "Underlying":
        if INDEX_CACHE.exists() and not force:
            try:
                cached = pd.read_parquet(INDEX_CACHE)
            except (ImportError, OSError, ValueError):
                cached = None                    # no parquet engine / corrupt cache -> refetch
            if cached is not None:
                cached["dt"] = pd.to_datetime(cached["dt"], utc=True)
                have_start, have_end = cached["dt"].min(), cached["dt"].max()
                if have_start <= start and have_end >= end:
                    return cls(cached)
        s = int(pd.Timestamp(start).timestamp())
        e = int(pd.Timestamp(end).timestamp())
        candles = _fetch_range(INDEX_SYMBOL, s, e, resolution="1h")
        if candles.empty:
            raise RuntimeError(f"No {INDEX_SYMBOL} candles returned for {start}..{end}")
        # Caching is best-effort: the live runner passes force=True and only needs the
        # candles in memory, so a missing parquet engine (pyarrow/fastparquet) must NOT
        # crash the service — just skip the cache write.
        try:
            candles.to_parquet(INDEX_CACHE, index=False)
        except (ImportError, OSError, ValueError):
            pass
        return cls(candles)

    def candles(self) -> pd.DataFrame:
        """Full 1h OHLC frame indexed by close time (for the SuperTrend filter)."""
        return self._c

    def spot_at(self, ts: pd.Timestamp) -> float:
        """Nearest candle close at/around ts (backward-then-nearest fill)."""
        # match resolutions: the parquet round-trip can leave the index at µs while
        # `ts` (Timestamp.now()) is ns — mismatched units break searchsorted.
        idx = self._c.index.as_unit("ns")
        pos = idx.searchsorted(pd.Timestamp(ts).as_unit("ns"), side="right") - 1
        if pos < 0:
            pos = 0
        return float(self._c["close"].iloc[pos])

    def settlement_price(self, expiry_date: pd.Timestamp) -> float:
        """Index price at 12:00 UTC on the expiry date (Delta settlement convention)."""
        settle_ts = pd.Timestamp(expiry_date).normalize() + pd.Timedelta(hours=SETTLEMENT_HOUR_UTC)
        return self.spot_at(settle_ts)
