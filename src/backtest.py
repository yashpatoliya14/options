"""Walk-forward driver for the SuperTrend-directional premium-harvest strategy.

Daily loop:
  1. Read the SuperTrend direction as-of the entry hour.
  2. Directional (future) leg: open a 1x index future aligned with the trend;
     close it when SuperTrend flips (then re-open the other way).
  3. Short-option (theta) leg: whenever no live short option is open, sell one on
     the trend side (put in a down-trend, call in an up-trend) at the nearest
     short-dated expiry and hold it to settlement.

Produces a list of Trade objects and a daily mark-to-market equity ledger.
"""
from __future__ import annotations

import pandas as pd

from config import CONFIG, SETTLEMENT_HOUR_UTC
from data_loader import build_tape_cache, list_expiries, load_expiry_trades
from underlying import Underlying
from supertrend import TrendFilter
from strategy import sell_short_option, close_future, Trade


def _settlement_ts(expiry: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(expiry).normalize() + pd.Timedelta(hours=SETTLEMENT_HOUR_UTC)


def _pick_expiry(expiries, settlements, asof, cfg):
    """Nearest expiry whose settlement is short_min..short_max days after asof."""
    best = None
    for e in expiries:
        dte = (settlements[e] - asof).total_seconds() / 86400.0
        if cfg.short_min_dte_days <= dte <= cfg.short_max_dte_days:
            if best is None or dte < best[1]:
                best = (e, dte)
    return best


def run_backtest(cfg=CONFIG, verbose: bool = True, *, und=None,
                 expiries=None, tape_cache=None):
    """Run the walk-forward. Optional preloaded `und` / `expiries` / `tape_cache`
    let a parameter sweep reuse the (expensive) data loads across configs.
    """
    build_tape_cache()
    if expiries is None:
        expiries = list_expiries()
    if not expiries:
        raise RuntimeError("No expiries found in tape cache.")
    settlements = {e: _settlement_ts(e) for e in expiries}

    first_settle = min(settlements.values())
    last_settle = max(settlements.values())
    if und is None:
        und = Underlying.load(start=first_settle - pd.Timedelta(days=30),
                              end=last_settle + pd.Timedelta(days=2))
    trend_filter = TrendFilter.build(und.candles(), cfg)

    # cache per-expiry tapes lazily (shared across sweep runs if provided)
    if tape_cache is None:
        tape_cache = {}

    def tape_for(e):
        if e not in tape_cache:
            tape_cache[e] = load_expiry_trades(e)
        return tape_cache[e]

    # daily grid from just after data start to just before the last settlement
    start_day = (first_settle.normalize() + pd.Timedelta(days=1, hours=cfg.entry_hour_utc))
    day = start_day
    trades: list[Trade] = []

    # open directional future: dict(side, entry_ts, entry_index, trend) or None
    fut = None
    option_open_until: pd.Timestamp | None = None   # settlement ts of the live short option

    ledger = []   # daily mark-to-market rows

    while day <= last_settle:
        asof = day
        trend = trend_filter.direction_at(asof)
        spot = und.spot_at(asof)

        if trend is not None:
            # --- directional future leg ---
            if cfg.trade_future_leg:
                if fut is not None and fut["side"] != trend:
                    tr = close_future(fut["side"], fut["entry_ts"], fut["entry_index"],
                                      asof, spot, fut["trend"], "trend_flip", cfg)
                    trades.append(tr)
                    if verbose:
                        print(f"{asof:%Y-%m-%d} FUTURE close side={fut['side']:+d} "
                              f"pnl=${tr.pnl:8.2f} [{tr.exit_reason}]")
                    fut = None
                if fut is None:
                    fut = {"side": trend, "entry_ts": asof, "entry_index": spot, "trend": trend}

            # --- short-option (theta) leg ---
            # roll on the morning of (or after) the live option's expiry day, so a
            # 1-DTE option is on essentially every day (continuous premium coverage).
            need_new = option_open_until is None or asof.normalize() >= option_open_until.normalize()
            if need_new:
                pick = _pick_expiry(expiries, settlements, asof, cfg)
                if pick is not None:
                    expiry, _dte = pick
                    tp = tape_for(expiry)
                    if not tp.empty:
                        opt = sell_short_option(tp, und, expiry, settlements[expiry],
                                                asof, trend, cfg)
                        if opt is not None:
                            trades.append(opt)
                            option_open_until = opt.exit_ts
                            if verbose:
                                print(f"{asof:%Y-%m-%d} SELL {opt.opt_type} {opt.strike} "
                                      f"prem=${opt.entry_price:7.1f} pnl=${opt.pnl:8.2f} "
                                      f"exp={expiry:%Y-%m-%d}")

        # --- daily mark-to-market equity ---
        realized = sum(t.pnl for t in trades if t.exit_ts <= asof)
        fut_unreal = 0.0
        if fut is not None:
            mult = cfg.lot_multiplier * cfg.lots
            fut_unreal = fut["side"] * (spot - fut["entry_index"]) * mult
        ledger.append({"date": asof, "spot": round(spot, 2), "trend": trend,
                       "realized": round(realized, 2),
                       "future_unrealized": round(fut_unreal, 2),
                       "equity": round(realized + fut_unreal, 2)})

        day = day + pd.Timedelta(days=1)

    # close any still-open future at the last spot
    if fut is not None:
        final_ts = last_settle
        tr = close_future(fut["side"], fut["entry_ts"], fut["entry_index"],
                          final_ts, und.spot_at(final_ts), fut["trend"], "data_end", cfg)
        trades.append(tr)

    ledger_df = pd.DataFrame(ledger)
    return trades, ledger_df, und
