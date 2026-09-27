"""Reconstruct an implied-vol / delta curve from the trade tape.

For a chosen expiry and an as-of window we take the volume-weighted average traded
price of each (type, strike), invert Black-76 to an implied vol using the fetched
forward, and compute delta. This is what lets us pick delta-based strikes and mark
legs when no fresh trade exists.
"""
from __future__ import annotations

import pandas as pd

from pricing import implied_vol, black76_delta, year_fraction


def vwap_price(trades: pd.DataFrame, opt_type: str, strike: int,
               start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """Volume-weighted avg traded price for one leg within [start, end]; None if no trades."""
    m = ((trades["opt_type"] == opt_type) & (trades["strike"] == strike)
         & (trades["ts"] >= start) & (trades["ts"] <= end))
    sub = trades.loc[m]
    if sub.empty or sub["size"].sum() <= 0:
        return None
    return float((sub["price"] * sub["size"]).sum() / sub["size"].sum())


def build_iv_curve(trades: pd.DataFrame, settlement_ts: pd.Timestamp, forward: float,
                   asof: pd.Timestamp, window_start: pd.Timestamp,
                   r: float = 0.0) -> pd.DataFrame:
    """Per (type, strike) VWAP, implied vol and delta over [window_start, asof]."""
    T = year_fraction(asof, settlement_ts)
    win = trades[(trades["ts"] >= window_start) & (trades["ts"] <= asof)]
    if win.empty:
        return pd.DataFrame(columns=["opt_type", "strike", "vwap", "volume", "iv", "delta"])

    grp = win.groupby(["opt_type", "strike"], observed=True)
    agg = grp.apply(
        lambda g: pd.Series({
            "vwap": (g["price"] * g["size"]).sum() / g["size"].sum(),
            "volume": g["size"].sum(),
        }),
        include_groups=False,
    ).reset_index()

    ivs, deltas = [], []
    for _, row in agg.iterrows():
        iv = implied_vol(row["vwap"], forward, row["strike"], T, row["opt_type"], r)
        ivs.append(iv)
        deltas.append(black76_delta(forward, row["strike"], T, iv, row["opt_type"], r)
                      if iv else None)
    agg["iv"] = ivs
    agg["delta"] = deltas
    return agg.dropna(subset=["iv", "delta"]).reset_index(drop=True)


def implied_forward(trades: pd.DataFrame, asof: pd.Timestamp, window_start: pd.Timestamp) -> float | None:
    """Cross-check forward via put-call parity on the most-traded near-ATM strike.

    F = K + (C - P) for a strike where both call and put trade (r~0). Picks the strike
    with the largest combined volume as the most reliable parity anchor.
    """
    win = trades[(trades["ts"] >= window_start) & (trades["ts"] <= asof)]
    if win.empty:
        return None
    piv = win.groupby(["strike", "opt_type"], observed=True).apply(
        lambda g: (g["price"] * g["size"]).sum() / g["size"].sum(), include_groups=False
    ).unstack("opt_type")
    vol = win.groupby("strike", observed=True)["size"].sum()
    if "C" not in piv.columns or "P" not in piv.columns:
        return None
    both = piv.dropna(subset=["C", "P"])
    if both.empty:
        return None
    best_strike = vol.reindex(both.index).idxmax()
    c = both.loc[best_strike, "C"]
    p = both.loc[best_strike, "P"]
    return float(best_strike + (c - p))
