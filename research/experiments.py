"""
Staged research experiments on the fast simulator.

Usage:
    python -m research.experiments baseline
    python -m research.experiments breakdown     # regime / P&L attribution
    python -m research.experiments filters       # each filter independently
    python -m research.experiments grid          # expiry / offset / TP / SL grids
    python -m research.experiments walkforward   # 6-fold walk-forward
    python -m research.experiments perturb       # parameter perturbation
    python -m research.experiments montecarlo    # confidence ranges

Design rules (per audit spec):
  - filters are tested independently, one at a time, vs baseline
  - every change must beat baseline OOS, not just in-sample
  - nothing here mutates engine defaults; final picks go through config
"""
from __future__ import annotations

import math
import random
import sys
from collections import defaultdict

import pandas as pd

from research import prepare, run_backtest, signals_from_frame, summarize

CACHE = "BTCUSD_1h_cache.csv"


def load_df():
    candles = pd.read_csv(CACHE)
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
    candles = candles.sort_values("timestamp").reset_index(drop=True)
    return prepare(candles)


def fmt(s: dict) -> str:
    pf = s["pf"] if s["pf"] != float("inf") else 99.9
    return (
        f"n={s['trades']:4d} wr={s['win_rate']:5.1%} pf={pf:5.2f} "
        f"exp={s['expectancy']:+.4f} net={s['net']:+8.3f} maxDD={s['max_dd_pct']:6.2f}%"
    )


def run(df, sigs, **kw):
    base = dict(tp_pct=0.60, sl_pct=1.50, cooldown_bars=6,
                expiry_selection="nearest_valid_after_signal", cutoff_hour=9,
                target_dte=1.0, min_dte=0.0, max_dte=14.0)
    base.update(kw)
    sim = run_backtest(df, sigs, **base)
    return sim["trades"]


# ---------------------------------------------------------------- baseline

def baseline(df):
    sigs = [s for s in signals_from_frame(df, 2, True) if s["idx"] >= 52]
    trades = run(df, sigs)
    print("BASELINE  ", fmt(summarize(trades, df["timestamp"].iloc[0], df["timestamp"].iloc[-1])))
    return trades


# ---------------------------------------------------------------- breakdown

def breakdown(df):
    sigs = [s for s in signals_from_frame(df, 2, True) if s["idx"] >= 52]
    trades = run(df, sigs)
    if not trades:
        print("no trades")
        return
    print(f"{'bucket':24} {'n':>4} {'wr':>6} {'pf':>6} {'avg':>9} {'net':>9}")
    buckets = {
        "bull(buy)": [t for t in trades if t["signal_type"] == "buy"],
        "bear(sell)": [t for t in trades if t["signal_type"] == "sell"],
        "hour 0-7 (asia)": [t for t in trades if 0 <= t["hour"] < 8],
        "hour 8-15 (eu)": [t for t in trades if 8 <= t["hour"] < 16],
        "hour 16-23 (us)": [t for t in trades if t["hour"] >= 16],
        "low vol (rv<0.4)": [t for t in trades if (t.get("rv") or 0) < 0.4],
        "high vol (rv>=0.4)": [t for t in trades if (t.get("rv") or 0) >= 0.4],
        "low atr% (<0.8%)": [t for t in trades if (t.get("atr_pct") or 0) < 0.008],
        "high atr% (>=0.8%)": [t for t in trades if (t.get("atr_pct") or 0) >= 0.008],
        "same_day expiry": [t for t in trades if t["expiry_type"] == "same_day"],
        "next_day expiry": [t for t in trades if t["expiry_type"] == "next_day"],
        "exit: TP": [t for t in trades if t["exit_reason"] == "profit_target"],
        "exit: SL": [t for t in trades if t["exit_reason"] == "stop_loss"],
        "exit: time": [t for t in trades if t["exit_reason"] == "time_exit"],
        "exit: expired": [t for t in trades if t["exit_reason"] == "expired"],
    }
    for name, rows in buckets.items():
        if not rows:
            print(f"{name:24} {0:4d}")
            continue
        s = summarize(rows, df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        pf = s["pf"] if s["pf"] != float("inf") else 99.9
        print(f"{name:24} {s['trades']:4d} {s['win_rate']:6.1%} {pf:6.2f} {s['expectancy']:+9.4f} {s['net']:+9.3f}")


# ---------------------------------------------------------------- filters

def filters(df):
    sigs_all = [s for s in signals_from_frame(df, 2, True) if s["idx"] >= 52]
    trades_base = run(df, sigs_all)
    base = summarize(trades_base, df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
    print("baseline:", fmt(base))
    print(f"\n{'filter':28} {'n':>4} {'removed':>8} {'wr_removed':>10} {'pf':>6} {'exp':>9} {'maxDD%':>8}")

    dfil = df.set_index("timestamp")

    def test(name, keep_fn):
        sigs = [s for s in sigs_all if keep_fn(s)]
        trades = run(df, sigs)
        s = summarize(trades, df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        removed = [t for t in trades_base if t["entry_idx"] not in {x["idx"] for x in sigs}]
        wr_rem = (sum(1 for t in removed if t["pnl"] > 0) / len(removed)) if removed else 0.0
        pf = s["pf"] if s["pf"] != float("inf") else 99.9
        print(f"{name:28} {s['trades']:4d} {len(removed):8d} {wr_rem:10.1%} {pf:6.2f} {s['expectancy']:+9.4f} {s['max_dd_pct']:8.2f}")

    test("rv > 0.35", lambda s: (s["rv"] or 0) > 0.35)
    test("rv > 0.45", lambda s: (s["rv"] or 0) > 0.45)
    test("rv > 0.55", lambda s: (s["rv"] or 0) > 0.55)
    test("rv percentile > 40%", lambda s: df["rv_pctile"].iloc[s["idx"]] > 0.40)
    test("rv percentile > 60%", lambda s: df["rv_pctile"].iloc[s["idx"]] > 0.60)
    test("atr% < 1.0%", lambda s: (s["atr_pct"] or 1) < 0.010)
    test("atr% < 1.2%", lambda s: (s["atr_pct"] or 1) < 0.012)
    test("atr% >= 0.5%", lambda s: (s["atr_pct"] or 0) >= 0.005)
    test("hour 6-20 UTC", lambda s: 6 <= s["timestamp"].hour <= 20)
    test("hour 8-18 UTC", lambda s: 8 <= s["timestamp"].hour <= 18)
    test("iv>rv (rv<0.5 est)", lambda s: (s["rv"] or 0) < 0.5)


# ---------------------------------------------------------------- grid

def grid(df):
    sigs = [s for s in signals_from_frame(df, 2, True) if s["idx"] >= 52]

    print("== TP grid (sl=1.5) ==")
    for tp in (0.25, 0.40, 0.50, 0.60, 0.75, 0.80):
        s = summarize(run(df, sigs, tp_pct=tp), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        print(f"  tp={tp:4.2f}  {fmt(s)}")

    print("== SL grid (tp=0.6) ==")
    for sl in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
        s = summarize(run(df, sigs, tp_pct=0.60, sl_pct=sl), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        print(f"  sl={sl:4.2f}  {fmt(s)}")

    print("== OTM offset grid ==")
    for off in (0.0, 0.005, 0.01, 0.015, 0.02, 0.03):
        s = summarize(run(df, sigs, offset_pct=off), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        print(f"  off={off:5.3f}  {fmt(s)}")

    print("== DTE target grid (nearest_valid mode) ==")
    for td in (0.5, 1.0, 2.0, 3.0, 5.0, 7.0):
        s = summarize(run(df, sigs, target_dte=td), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        print(f"  target_dte={td:4.1f}  {fmt(s)}")

    print("== max_hold grid ==")
    for mh in (0, 6, 12, 24, 48):
        s = summarize(run(df, sigs, max_hold_bars=mh), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        print(f"  max_hold={mh:2d}h  {fmt(s)}")

    print("== exit_on_opposite ==")
    for eo in (False, True):
        s = summarize(run(df, sigs, exit_on_opposite=eo), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
        print(f"  opp={eo}  {fmt(s)}")


# ---------------------------------------------------------------- walk-forward

def walkforward(df, n_folds=6, train_frac=0.7):
    """Split data into n_folds sequential folds; pick params on the train
    segment of each fold, evaluate once on its test segment."""
    n = len(df)
    fold_len = n // n_folds
    results = []
    configs = {
        "base": dict(),
        "tp40": dict(tp_pct=0.40),
        "tp75": dict(tp_pct=0.75),
        "sl100": dict(sl_pct=1.00),
        "sl200": dict(sl_pct=2.00),
        "off1": dict(offset_pct=0.01),
        "off2": dict(offset_pct=0.02),
        "dte1": dict(target_dte=1.0),
        "dte3": dict(target_dte=3.0),
        "dte7": dict(target_dte=7.0),
        "hold12": dict(max_hold_bars=12),
        "hold48": dict(max_hold_bars=48),
        "rv45filt": dict(signal_filter=lambda s: (s.get("rv") or 0) > 0.45),
    }
    keys = list(configs)
    for f in range(n_folds):
        start = f * fold_len
        split = start + int(fold_len * train_frac)
        end = min(start + fold_len, n)
        if split >= end - 60:
            continue
        train = df.iloc[start:split].reset_index(drop=True)
        test = df.iloc[split:end].reset_index(drop=True)
        sigs_tr = [s for s in signals_from_frame(train, 2, True) if s["idx"] >= 52]
        sigs_te = [s for s in signals_from_frame(test, 2, True) if s["idx"] >= 52]
        # rank configs on train
        scores = {}
        for k in keys:
            tr = run(train, sigs_tr, **configs[k])
            st = summarize(tr, train["timestamp"].iloc[0], train["timestamp"].iloc[-1])
            scores[k] = st["expectancy"] if st["trades"] >= 8 else -9e9
        best = max(scores, key=scores.get)
        te = run(test, sigs_te, **configs[best])
        st = summarize(te, test["timestamp"].iloc[0], test["timestamp"].iloc[-1])
        results.append((f, best, st))
        print(f"fold {f}: train_picks={best:10s} test: {fmt(st)}")
    # aggregate
    trades = [t for _, _, st in results for t in []]  # summarized already
    pnls = []
    wins = 0
    total = 0
    gross_win = gross_loss = 0.0
    for _, _, st in results:
        pass
    print("\nwalk-forward picks:", [r[1] for r in results])
    return results


# ---------------------------------------------------------------- perturbation

def perturb(df):
    """Robustness: neighborhood of parameters must not collapse."""
    sigs = [s for s in signals_from_frame(df, 2, True) if s["idx"] >= 52]
    print("== perturbation: TP x SL ==")
    print(f"{'':8}" + "".join(f"sl={sl:<5.2f}" for sl in (1.0, 1.25, 1.5, 1.75, 2.0)))
    for tp in (0.40, 0.50, 0.60, 0.75):
        row = []
        for sl in (1.0, 1.25, 1.5, 1.75, 2.0):
            s = summarize(run(df, sigs, tp_pct=tp, sl_pct=sl), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
            row.append(f"{s['expectancy']:+.4f}")
        print(f"tp={tp:<5.2f}" + "".join(f"{v:>8}" for v in row))

    print("\n== perturbation: offset x target_dte ==")
    print(f"{'':10}" + "".join(f"dte={d:<5.1f}" for d in (0.5, 1.0, 2.0, 3.0)))
    for off in (0.005, 0.01, 0.015, 0.02):
        row = []
        for d in (0.5, 1.0, 2.0, 3.0):
            s = summarize(run(df, sigs, offset_pct=off, target_dte=d), df["timestamp"].iloc[0], df["timestamp"].iloc[-1])
            row.append(f"{s['expectancy']:+.4f}")
        print(f"off={off:<6.3f}" + "".join(f"{v:>8}" for v in row))


# ---------------------------------------------------------------- monte carlo

def montecarlo(df, n_sims=2000, seed=42):
    sigs = [s for s in signals_from_frame(df, 2, True) if s["idx"] >= 52]
    trades = run(df, sigs)
    if not trades:
        print("no trades")
        return
    pnls = [t["pnl"] for t in trades]
    rng = random.Random(seed)
    stats = {"cagr": [], "max_dd_pct": [], "pf": [], "p_loss": []}
    capital = 10.0
    for _ in range(n_sims):
        shuffled = pnls[:]
        rng.shuffle(shuffled)
        eq = capital
        peak = capital
        mdd = 0.0
        for p in shuffled:
            eq += p
            peak = max(peak, eq)
            mdd = min(mdd, (eq - peak) / peak)
        gp = sum(p for p in shuffled if p > 0)
        gl = abs(sum(p for p in shuffled if p < 0))
        stats["max_dd_pct"].append(mdd * 100)
        stats["pf"].append(gp / gl if gl else 99.0)
        stats["p_loss"].append(1.0 if sum(shuffled) < 0 else 0.0)
        years = 1666.625 / 365.25
        end = capital + sum(shuffled)
        stats["cagr"].append(((end / capital) ** (1 / years) - 1) * 100 if end > 0 else -100.0)

    def pct(arr, q):
        arr = sorted(arr)
        return arr[int(q * (len(arr) - 1))]

    print("Monte Carlo (trade-order randomization, %d sims):" % n_sims)
    print(f"  CAGR      p5={pct(stats['cagr'],0.05):8.2f}%  p50={pct(stats['cagr'],0.5):8.2f}%  p95={pct(stats['cagr'],0.95):8.2f}%")
    print(f"  MaxDD     p5={pct(stats['max_dd_pct'],0.05):8.2f}%  p50={pct(stats['max_dd_pct'],0.5):8.2f}%  p95={pct(stats['max_dd_pct'],0.95):8.2f}%")
    print(f"  PF        p5={pct(stats['pf'],0.05):8.2f}  p50={pct(stats['pf'],0.5):8.2f}  p95={pct(stats['pf'],0.95):8.2f}")
    print(f"  P(negative total) = {sum(stats['p_loss'])/len(stats['p_loss']):.1%}")


# ---------------------------------------------------------------- main

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    df = load_df()
    if cmd in ("baseline", "all"):
        print("\n=== BASELINE ===")
        baseline(df)
    if cmd in ("breakdown", "all"):
        print("\n=== REGIME BREAKDOWN ===")
        breakdown(df)
    if cmd in ("filters", "all"):
        print("\n=== FILTERS (independent) ===")
        filters(df)
    if cmd in ("grid", "all"):
        print("\n=== PARAMETER GRIDS ===")
        grid(df)
    if cmd in ("walkforward", "all"):
        print("\n=== WALK-FORWARD ===")
        walkforward(df)
    if cmd in ("perturb", "all"):
        print("\n=== PERTURBATION ===")
        perturb(df)
    if cmd in ("montecarlo", "all"):
        print("\n=== MONTE CARLO ===")
        montecarlo(df)


if __name__ == "__main__":
    main()
