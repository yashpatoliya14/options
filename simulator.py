"""Backtest simulator for the SuperTrend 0DTE covered-directional strategy.

This simulates *exactly* the live logic in start_algo.py + src/expiry.py, day by
day over the 2026 tape, starting from $100 of capital:

  ENTRY (when flat, at 08:00 UTC):
    trend = SuperTrend direction on the higher-timeframe index.
      BULLISH -> LONG 1 future @ spot   + SELL 1 ATM 0DTE CALL
      BEARISH -> SHORT 1 future @ spot  + SELL 1 ATM 0DTE PUT
    A stop-loss is placed at the SuperTrend line (trails each day).

  INTRADAY:
    If price crosses the SuperTrend stop line, the future is stopped out
    (this is the trend-flip exit). The 0DTE option still settles at 12:00.

  0DTE EXPIRY (12:00 UTC), if the future survived:
    BULLISH: spot > future entry -> CLOSE the future (take profit), go flat.
             else                -> ROLL: keep the future, sell a new CALL
                                    struck at the future ENTRY price (now OTM).
    BEARISH: spot < future entry -> CLOSE the future, go flat.
             else                -> ROLL: sell a new PUT at the future entry.

  Option P&L (short): (premium - intrinsic_at_settle) * mult - fees.
  Future  P&L:        side * (exit - entry) * mult - fees.

Outputs: results/simulation_results.md, results/simulation_equity.csv (equity
curve), an optional PNG, and a printed summary. Capital starts at $100.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import numpy as np
import pandas as pd

from config import CONFIG, RESULTS_DIR, SETTLEMENT_HOUR_UTC
from data_loader import build_tape_cache, list_expiries, load_expiry_trades
from underlying import Underlying
from supertrend import TrendFilter
from strategy import option_fee, future_fee

CAPITAL = 100.0
STRIKE_STEP = 200


def _round_strike(px: float, step: int = STRIKE_STEP) -> int:
    return int(round(px / step) * step)


def _price_short_option(tape: pd.DataFrame, opt_type: str, target_strike: int,
                        asof: pd.Timestamp, window_h: int):
    """VWAP premium of the traded strike nearest `target_strike` for `opt_type`
    within [asof-window, asof]. Returns (strike, premium) or None."""
    lo = asof - pd.Timedelta(hours=window_h)
    win = tape[(tape["opt_type"] == opt_type) & (tape["ts"] >= lo) & (tape["ts"] <= asof)]
    if win.empty or win["size"].sum() <= 0:
        return None
    grp = win.groupby("strike")
    vwap = (grp.apply(lambda g: (g["price"] * g["size"]).sum() / g["size"].sum(),
                      include_groups=False))
    strikes = vwap.index.to_numpy()
    k = int(strikes[np.abs(strikes - target_strike).argmin()])
    prem = float(vwap.loc[k])
    return (k, prem) if prem > 0 else None


def _stop_hit(candles: pd.DataFrame, side: int, sl: float,
              t0: pd.Timestamp, t1: pd.Timestamp) -> bool:
    """Did price breach the stop line between t0 and t1 (hourly extremes)?
    Long is stopped when low <= sl; short when high >= sl."""
    win = candles[(candles.index > t0) & (candles.index <= t1)]
    if win.empty:
        return False
    if side > 0:
        return bool((win["low"] <= sl).any())
    return bool((win["high"] >= sl).any())


def run_simulation(cfg=CONFIG, verbose: bool = False):
    build_tape_cache()
    expiries = [e for e in list_expiries() if e.year == 2026]
    if not expiries:
        raise RuntimeError("No 2026 expiries in tape cache.")

    first, last = min(expiries), max(expiries)
    und = Underlying.load(start=first - pd.Timedelta(days=30),
                          end=last + pd.Timedelta(days=2))
    candles = und.candles()
    # only simulate expiries whose 12:00 settlement has index coverage (the daily
    # 0DTE tape + index run Jan–late Sep; later weekly/monthly expiries would settle
    # against a stale last price, so drop them).
    cov = candles.index.max()
    expiries = [e for e in expiries
                if e.normalize() + pd.Timedelta(hours=SETTLEMENT_HOUR_UTC) <= cov]
    tf = TrendFilter.build(candles, cfg)
    mult = cfg.lot_multiplier * cfg.lots

    trades: list[dict] = []
    ledger: list[dict] = []
    fut = None                 # {side, entry, entry_ts, sl, bias, ts_last}
    realized = 0.0

    for d in expiries:
        entry_ts = d.normalize() + pd.Timedelta(hours=cfg.entry_hour_utc)
        settle_ts = d.normalize() + pd.Timedelta(hours=SETTLEMENT_HOUR_UTC)
        trend = tf.direction_at(entry_ts)
        spot_am = und.spot_at(entry_ts)
        spot_settle = und.settlement_price(d)

        # (a) trailing stop on an open future
        if fut is not None:
            line = tf.stop_level_at(entry_ts)
            if line is not None:
                fut["sl"] = line
            if _stop_hit(candles, fut["side"], fut["sl"], fut["ts_last"], settle_ts):
                fees = future_fee(fut["entry"], cfg) + future_fee(fut["sl"], cfg)
                pnl = fut["side"] * (fut["sl"] - fut["entry"]) * mult - fees
                realized += pnl
                trades.append(dict(kind="future", side=fut["side"], opt_type=None,
                                   strike=None, entry_ts=fut["entry_ts"], exit_ts=entry_ts,
                                   entry_price=fut["entry"], exit_price=fut["sl"],
                                   fees=fees, pnl=pnl, reason="stop_loss"))
                if verbose:
                    print(f"{d:%Y-%m-%d} STOP  fut {fut['side']:+d} @ {fut['sl']:,.0f} pnl=${pnl:+.3f}")
                fut = None

        # (b) open a fresh position when flat
        if fut is None and trend is not None:
            line = tf.stop_level_at(entry_ts)
            bias = "BULLISH" if trend > 0 else "BEARISH"
            sl = line if line is not None else spot_am * (0.98 if trend > 0 else 1.02)
            fut = dict(side=trend, entry=spot_am, entry_ts=entry_ts, sl=sl,
                       bias=bias, ts_last=entry_ts)

        # (c) sell the 0DTE covered option at the future's entry strike
        if fut is not None:
            opt_type = "C" if fut["bias"] == "BULLISH" else "P"
            target = _round_strike(fut["entry"])
            tape = load_expiry_trades(d)
            res = None if tape.empty else _price_short_option(
                tape, opt_type, target, entry_ts, cfg.entry_window_hours)
            if res is not None:
                k, prem = res
                intrinsic = (max(spot_settle - k, 0.0) if opt_type == "C"
                             else max(k - spot_settle, 0.0))
                fees = option_fee(prem, spot_am, cfg) + (
                    option_fee(intrinsic, spot_settle, cfg) if intrinsic > 0 else 0.0)
                pnl = (prem - intrinsic) * mult - fees
                realized += pnl
                trades.append(dict(kind="option", side=-1, opt_type=opt_type, strike=k,
                                   entry_ts=entry_ts, exit_ts=settle_ts,
                                   entry_price=prem, exit_price=intrinsic,
                                   fees=fees, pnl=pnl, reason="expiry"))
                if verbose:
                    print(f"{d:%Y-%m-%d} SELL {opt_type} {k} prem=${prem:.1f} "
                          f"intr=${intrinsic:.1f} pnl=${pnl:+.3f}")

        # (d) 0DTE expiry decision on the future (take profit or roll)
        if fut is not None:
            profit = (spot_settle > fut["entry"]) if fut["bias"] == "BULLISH" \
                else (spot_settle < fut["entry"])
            if profit:
                fees = future_fee(fut["entry"], cfg) + future_fee(spot_settle, cfg)
                pnl = fut["side"] * (spot_settle - fut["entry"]) * mult - fees
                realized += pnl
                trades.append(dict(kind="future", side=fut["side"], opt_type=None,
                                   strike=None, entry_ts=fut["entry_ts"], exit_ts=settle_ts,
                                   entry_price=fut["entry"], exit_price=spot_settle,
                                   fees=fees, pnl=pnl, reason="take_profit"))
                if verbose:
                    print(f"{d:%Y-%m-%d} CLOSE fut {fut['side']:+d} @ {spot_settle:,.0f} pnl=${pnl:+.3f}")
                fut = None
            else:
                fut["ts_last"] = settle_ts

        # (e) daily mark-to-market equity
        unreal = fut["side"] * (spot_settle - fut["entry"]) * mult if fut else 0.0
        ledger.append(dict(date=settle_ts, spot=round(spot_settle, 2), trend=trend,
                           realized=round(realized, 4),
                           unrealized=round(unreal, 4),
                           equity=round(CAPITAL + realized + unreal, 4)))

    # close any still-open future at the last settlement
    if fut is not None:
        fees = future_fee(fut["entry"], cfg) + future_fee(spot_settle, cfg)
        pnl = fut["side"] * (spot_settle - fut["entry"]) * mult - fees
        realized += pnl
        trades.append(dict(kind="future", side=fut["side"], opt_type=None, strike=None,
                           entry_ts=fut["entry_ts"], exit_ts=settle_ts,
                           entry_price=fut["entry"], exit_price=spot_settle,
                           fees=fees, pnl=pnl, reason="data_end"))

    return pd.DataFrame(trades), pd.DataFrame(ledger)


def _stats(trades: pd.DataFrame, ledger: pd.DataFrame) -> dict:
    eq = ledger["equity"].to_numpy()
    peak = np.maximum.accumulate(eq)
    max_dd = float((peak - eq).max()) if len(eq) else 0.0
    max_dd_pct = float((((peak - eq) / peak) * 100).max()) if len(eq) else 0.0
    opt = trades[trades["kind"] == "option"]
    fut = trades[trades["kind"] == "future"]
    n = len(trades)
    wins = int((trades["pnl"] > 0).sum())
    end_eq = float(eq[-1]) if len(eq) else CAPITAL
    return {
        "n": n, "wins": wins,
        "win_rate": 100.0 * wins / n if n else 0.0,
        "total": float(trades["pnl"].sum()) if n else 0.0,
        "option_pnl": float(opt["pnl"].sum()) if len(opt) else 0.0,
        "future_pnl": float(fut["pnl"].sum()) if len(fut) else 0.0,
        "n_options": len(opt), "n_futures": len(fut),
        "opt_win_rate": 100.0 * (opt["pnl"] > 0).mean() if len(opt) else 0.0,
        "fut_win_rate": 100.0 * (fut["pnl"] > 0).mean() if len(fut) else 0.0,
        "n_stops": int((fut["reason"] == "stop_loss").sum()) if len(fut) else 0,
        "n_tp": int((fut["reason"] == "take_profit").sum()) if len(fut) else 0,
        "total_fees": float(trades["fees"].sum()) if n else 0.0,
        "max_dd": max_dd, "max_dd_pct": max_dd_pct,
        "start_eq": CAPITAL, "end_eq": end_eq,
        "ret_pct": 100.0 * (end_eq - CAPITAL) / CAPITAL,
    }


def _save_equity_png(ledger: pd.DataFrame) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(ledger["date"], ledger["equity"], color="#1f77b4", lw=1.4)
    ax.axhline(CAPITAL, color="grey", ls="--", lw=0.8)
    ax.set_title("Equity curve — SuperTrend 0DTE covered-directional ($100 start)")
    ax.set_xlabel("Date"); ax.set_ylabel("Equity ($)")
    fig.autofmt_xdate(); fig.tight_layout()
    fig.savefig(RESULTS_DIR / "simulation_equity.png", dpi=110)
    plt.close(fig)
    return True


def write_report(trades: pd.DataFrame, ledger: pd.DataFrame, cfg=CONFIG) -> dict:
    s = _stats(trades, ledger)
    period = f"{ledger['date'].min():%Y-%m-%d} → {ledger['date'].max():%Y-%m-%d}"
    have_png = _save_equity_png(ledger)
    ledger.to_csv(RESULTS_DIR / "simulation_equity.csv", index=False)
    trades.to_csv(RESULTS_DIR / "simulation_trades.csv", index=False)

    md = []
    md.append("# SuperTrend 0DTE Covered-Directional — Simulation Results\n")
    md.append("## Strategy simulated\n")
    md.append(
        "Exactly the live logic in `start_algo.py` + `src/expiry.py`, walked day by day "
        "over the 2026 tape from **$100** of starting capital.\n\n"
        "- **Entry (when flat, 08:00 UTC):** SuperTrend "
        f"({cfg.st_timeframe_hours}h, ATR{cfg.st_atr_period}, ×{cfg.st_multiplier}) sets the bias. "
        "Bullish → LONG 1 future + SELL 1 ATM 0DTE **CALL**; bearish → SHORT 1 future + SELL 1 "
        "ATM 0DTE **PUT**. Stop-loss sits at the SuperTrend line (trails daily).\n"
        "- **Intraday:** if price crosses the SuperTrend stop line the future is stopped out "
        "(the trend-flip exit). The 0DTE option still settles at 12:00 UTC.\n"
        "- **0DTE expiry (12:00 UTC), future surviving:** bullish & spot > entry → CLOSE the "
        "future (take profit); else ROLL — keep the future and sell a new option struck at the "
        "future **entry price** (now OTM). Bearish is the mirror.\n"
        f"- **Sizing:** 1 contract/leg × {cfg.lot_multiplier} BTC. Fees {cfg.fee_rate*100:.3f}% "
        f"notional, option fee capped at {cfg.fee_cap_frac*100:.0f}% of premium.\n")

    md.append("\n## Results\n")
    md.append(f"**Period:** {period}\n")
    md.append(
        "| Metric | Value |\n| --- | --- |\n"
        f"| Starting capital | ${s['start_eq']:,.2f} |\n"
        f"| **Ending equity** | **${s['end_eq']:,.2f}** |\n"
        f"| **Total return** | **{s['ret_pct']:+.2f}%** |\n"
        f"| Total P&L | ${s['total']:+,.2f} |\n"
        f"| Total trades | {s['n']} ({s['n_options']} options, {s['n_futures']} futures) |\n"
        f"| Win rate | {s['wins']}/{s['n']} ({s['win_rate']:.1f}%) |\n"
        f"| Premium (option) P&L | ${s['option_pnl']:+,.2f} (win {s['opt_win_rate']:.1f}%) |\n"
        f"| Directional (future) P&L | ${s['future_pnl']:+,.2f} (win {s['fut_win_rate']:.1f}%) |\n"
        f"| Future exits | {s['n_tp']} take-profit / {s['n_stops']} stop-loss |\n"
        f"| Total fees | ${s['total_fees']:,.2f} |\n"
        f"| Max drawdown | ${s['max_dd']:,.2f} ({s['max_dd_pct']:.2f}%) |\n")

    md.append("\n## Equity curve\n")
    if have_png:
        md.append("![Equity curve](simulation_equity.png)\n")
    md.append("\nFull daily equity in [`simulation_equity.csv`](simulation_equity.csv); "
              "per-trade detail in [`simulation_trades.csv`](simulation_trades.csv).\n")

    # compact monthly equity checkpoints so the curve is readable inline
    md.append("\n### Monthly equity checkpoints\n")
    mc = ledger.copy()
    mc["month"] = pd.to_datetime(mc["date"]).dt.strftime("%Y-%m")
    monthly = mc.groupby("month")["equity"].last()
    md.append("| Month-end | Equity |\n| --- | --- |\n"
              + "\n".join(f"| {m} | ${v:,.2f} |" for m, v in monthly.items()) + "\n")

    (RESULTS_DIR / "simulation_results.md").write_text("\n".join(md), encoding="utf-8")
    return s


if __name__ == "__main__":
    verbose = "-v" in sys.argv[1:]
    trades, ledger = run_simulation(verbose=verbose)
    s = write_report(trades, ledger)
    print("=" * 60)
    print("SuperTrend 0DTE Covered-Directional — Simulation ($100 start)")
    print("=" * 60)
    print(f"Period            : {ledger['date'].min():%Y-%m-%d} -> {ledger['date'].max():%Y-%m-%d}")
    print(f"Trades            : {s['n']} ({s['n_options']} opt, {s['n_futures']} fut)")
    print(f"Win rate          : {s['wins']}/{s['n']} ({s['win_rate']:.1f}%)")
    print(f"Option P&L        : ${s['option_pnl']:+.2f}  (win {s['opt_win_rate']:.1f}%)")
    print(f"Future P&L        : ${s['future_pnl']:+.2f}  (win {s['fut_win_rate']:.1f}%)")
    print(f"Future exits      : {s['n_tp']} TP / {s['n_stops']} SL")
    print(f"Total fees        : ${s['total_fees']:.2f}")
    print(f"Max drawdown      : ${s['max_dd']:.2f} ({s['max_dd_pct']:.2f}%)")
    print(f"Total P&L         : ${s['total']:+.2f}")
    print(f"Ending equity     : ${s['end_eq']:.2f}  ({s['ret_pct']:+.2f}%)")
    print("=" * 60)
    print("Wrote results/simulation_results.md, simulation_equity.csv, simulation_trades.csv")



