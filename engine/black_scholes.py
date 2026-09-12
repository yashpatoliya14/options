from __future__ import annotations

import math
from statistics import NormalDist

_NORMAL = NormalDist()


def _d1_d2(spot: float, strike: float, t_years: float, iv: float, rate: float) -> tuple[float, float]:
    if spot <= 0 or strike <= 0 or t_years <= 0 or iv <= 0:
        return float("nan"), float("nan")
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return d1, d2


def price(spot: float, strike: float, t_years: float, iv: float, rate: float, option_type: str) -> float:
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 0:
        if option_type == "call":
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    d1, d2 = _d1_d2(spot, strike, t_years, iv, rate)
    if option_type == "call":
        return spot * _NORMAL.cdf(d1) - strike * math.exp(-rate * t_years) * _NORMAL.cdf(d2)
    return strike * math.exp(-rate * t_years) * _NORMAL.cdf(-d2) - spot * _NORMAL.cdf(-d1)


def greeks(spot: float, strike: float, t_years: float, iv: float, rate: float, option_type: str) -> dict[str, float]:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        intrinsic = price(spot, strike, t_years, iv, rate, option_type)
        delta = 0.0
        if option_type == "call":
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "price": intrinsic}

    d1, d2 = _d1_d2(spot, strike, t_years, iv, rate)
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    disc = math.exp(-rate * t_years)
    gamma = pdf / (spot * iv * math.sqrt(t_years))
    vega = spot * pdf * math.sqrt(t_years) / 100.0
    if option_type == "call":
        delta = _NORMAL.cdf(d1)
        theta = (
            -(spot * pdf * iv) / (2.0 * math.sqrt(t_years))
            - rate * strike * disc * _NORMAL.cdf(d2)
        ) / 365.0
    else:
        delta = _NORMAL.cdf(d1) - 1.0
        theta = (
            -(spot * pdf * iv) / (2.0 * math.sqrt(t_years))
            + rate * strike * disc * _NORMAL.cdf(-d2)
        ) / 365.0
    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "theta": float(theta),
        "vega": float(vega),
        "price": price(spot, strike, t_years, iv, rate, option_type),
    }
