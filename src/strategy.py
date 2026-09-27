"""Directional covered-option legs: strike selection, short-option settlement, future P&L.

All P&L is in USD. Tape premiums are USD per 1 BTC of underlying, so every cash
figure is scaled by mult = lot_multiplier * lots (0.001 * lots by default).

Two leg types make up the strategy:
  * option  — a SHORT option (put in a down-trend, call in an up-trend) sold and
              held to its own expiry; P&L = premium - intrinsic_at_expiry - fees.
  * future  — a 1x index position aligned with the SuperTrend, opened when a
              trend starts and closed when it flips; P&L = side*(exit-entry) - fees.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from config import StrategyConfig
from strategy_chain import build_iv_curve, vwap_price


@dataclass
class Trade:
    kind: str                       # "option" | "future"
    side: int                       # -1 short / +1 long
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float              # option: premium/BTC ; future: index level
    exit_price: float               # option: intrinsic/BTC ; future: index level
    trend: int                      # SuperTrend direction at open (+1/-1)
    fees: float
    pnl: float
    exit_reason: str
    opt_type: str | None = None
    strike: int | None = None
    diagnostics: dict = field(default_factory=dict)


def option_fee(premium: float, forward: float, cfg: StrategyConfig) -> float:
    """Delta options fee: fee_rate of notional, capped at fee_cap_frac of premium (USD)."""
    mult = cfg.lot_multiplier * cfg.lots
    notional_fee = cfg.fee_rate * forward * mult
    premium_cap = cfg.fee_cap_frac * abs(premium) * mult
    return min(notional_fee, premium_cap)


def future_fee(index: float, cfg: StrategyConfig) -> float:
    """Taker fee on the future notional (per side)."""
    mult = cfg.lot_multiplier * cfg.lots
    return cfg.fee_rate * index * mult


def _nearest_by_delta(curve: pd.DataFrame, opt_type: str, target_abs_delta: float):
    sub = curve[curve["opt_type"] == opt_type].copy()
    if sub.empty:
        return None
    sub["dist"] = (sub["delta"].abs() - target_abs_delta).abs()
    return sub.sort_values("dist").iloc[0]


def sell_short_option(trades: pd.DataFrame, underlying, expiry: pd.Timestamp,
                      settlement_ts: pd.Timestamp, asof: pd.Timestamp,
                      trend: int, cfg: StrategyConfig) -> Trade | None:
    """Sell one short-dated option on the trend side and settle it at its expiry.

    down-trend (trend=-1) -> sell a PUT   (covered put)
    up-trend   (trend=+1) -> sell a CALL  (covered call)
    """
    mult = cfg.lot_multiplier * cfg.lots
    opt_type = "P" if trend < 0 else "C"

    forward = underlying.spot_at(asof)
    window_start = asof - pd.Timedelta(hours=cfg.entry_window_hours)
    curve = build_iv_curve(trades, settlement_ts, forward, asof, window_start,
                           cfg.risk_free_rate)
    if len(curve) < cfg.min_strikes_for_curve:
        return None
    pick = _nearest_by_delta(curve, opt_type, cfg.short_target_delta)
    if pick is None:
        return None

    strike = int(pick["strike"])
    premium = float(pick["vwap"])
    if premium <= 0:
        return None

    entry_fee = option_fee(premium, forward, cfg)
    # hold to expiry: settle at index intrinsic
    S = underlying.settlement_price(expiry)
    intrinsic = max(S - strike, 0.0) if opt_type == "C" else max(strike - S, 0.0)
    exit_fee = option_fee(intrinsic, S, cfg) if intrinsic > 0 else 0.0

    # short option: collect premium, pay intrinsic at expiry
    pnl = (premium - intrinsic) * mult - entry_fee - exit_fee
    return Trade(
        kind="option", side=-1, entry_ts=asof, exit_ts=settlement_ts,
        entry_price=premium, exit_price=intrinsic, trend=trend,
        fees=entry_fee + exit_fee, pnl=pnl, exit_reason="expiry",
        opt_type=opt_type, strike=strike,
        diagnostics={"forward": round(forward, 2), "settle": round(float(S), 2),
                     "iv": round(float(pick["iv"]), 4), "delta": round(float(pick["delta"]), 3),
                     "n_curve_strikes": len(curve)},
    )


def close_future(side: int, entry_ts: pd.Timestamp, entry_index: float,
                 exit_ts: pd.Timestamp, exit_index: float, trend: int,
                 reason: str, cfg: StrategyConfig) -> Trade:
    """Realize the directional future leg. P&L = side*(exit-entry)*mult - fees."""
    mult = cfg.lot_multiplier * cfg.lots
    fees = future_fee(entry_index, cfg) + future_fee(exit_index, cfg)
    pnl = side * (exit_index - entry_index) * mult - fees
    return Trade(
        kind="future", side=side, entry_ts=entry_ts, exit_ts=exit_ts,
        entry_price=entry_index, exit_price=exit_index, trend=trend,
        fees=fees, pnl=pnl, exit_reason=reason,
        diagnostics={},
    )
