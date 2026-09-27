"""Black-76 option pricing, implied vol and greeks.

Black-76 prices European options on a forward F (appropriate for crypto where we
work off a forward/spot with ~0 carry). Premiums in the tape are USD per 1 BTC of
underlying; the lot multiplier (0.001) is applied later in P&L, not here.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq


def _d1_d2(F: float, K: float, T: float, sigma: float):
    if sigma <= 0 or T <= 0 or F <= 0 or K <= 0:
        return None, None
    vol_sqrt_t = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def black76_price(F: float, K: float, T: float, sigma: float, opt_type: str, r: float = 0.0) -> float:
    """Undiscounted-forward Black-76 price (discounted by exp(-rT))."""
    if T <= 0:
        # intrinsic at/after expiry
        intrinsic = max(F - K, 0.0) if opt_type == "C" else max(K - F, 0.0)
        return intrinsic
    d1, d2 = _d1_d2(F, K, T, sigma)
    disc = math.exp(-r * T)
    if opt_type == "C":
        return disc * (F * norm.cdf(d1) - K * norm.cdf(d2))
    return disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1))


def black76_delta(F: float, K: float, T: float, sigma: float, opt_type: str, r: float = 0.0) -> float:
    """Delta w.r.t. the forward. Call in (0,1), put in (-1,0)."""
    if T <= 0 or sigma <= 0:
        if opt_type == "C":
            return 1.0 if F > K else 0.0
        return -1.0 if F < K else 0.0
    d1, _ = _d1_d2(F, K, T, sigma)
    disc = math.exp(-r * T)
    if opt_type == "C":
        return disc * norm.cdf(d1)
    return -disc * norm.cdf(-d1)


def implied_vol(price: float, F: float, K: float, T: float, opt_type: str,
                r: float = 0.0) -> Optional[float]:
    """Invert Black-76 for sigma. Returns None if no valid IV (e.g. price below intrinsic)."""
    if price <= 0 or T <= 0 or F <= 0 or K <= 0:
        return None
    disc = math.exp(-r * T)
    intrinsic = disc * (max(F - K, 0.0) if opt_type == "C" else max(K - F, 0.0))
    # tiny tolerance: traded prints can sit a hair below theoretical intrinsic
    if price < intrinsic - 1e-6:
        return None
    upper_bound = disc * (F if opt_type == "C" else K)
    if price >= upper_bound:
        return None

    def objective(sigma: float) -> float:
        return black76_price(F, K, T, sigma, opt_type, r) - price

    try:
        return brentq(objective, 1e-4, 10.0, maxiter=200, xtol=1e-6)
    except (ValueError, RuntimeError):
        return None


def year_fraction(entry_ts, expiry_ts) -> float:
    """T in years (365-day) between two timezone-aware/naive UTC timestamps."""
    seconds = (expiry_ts - entry_ts).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24 * 3600)
