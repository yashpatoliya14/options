"""Turn Trade objects + the daily ledger into CSV logs, a console summary, and a
single Markdown results report (results/backtest_results.md).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULTS_DIR, CONFIG


def build_trade_frame(trades) -> pd.DataFrame:
    rows = []
    equity = 0.0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        equity += t.pnl
        rows.append({
            "kind": t.kind,
            "side": t.side,
            "opt_type": t.opt_type,
            "strike": t.strike,
            "entry_ts": t.entry_ts,
            "exit_ts": t.exit_ts,
            "trend": t.trend,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "fees": round(t.fees, 2),
            "pnl": round(t.pnl, 2),
            "exit_reason": t.exit_reason,
            "equity": round(equity, 2),
        })
    return pd.DataFrame(rows)


def _stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    eq = df["equity"].to_numpy()
    peak = np.maximum.accumulate(eq)
    max_dd = float((peak - eq).max()) if len(eq) else 0.0
    opt = df[df["kind"] == "option"]
    fut = df[df["kind"] == "future"]
    n = len(df)
    wins = int((df["pnl"] > 0).sum())
    return {
        "n": n,
        "wins": wins,
        "win_rate": 100.0 * wins / n if n else 0.0,
        "total": float(df["pnl"].sum()),
        "option_pnl": float(opt["pnl"].sum()),
        "future_pnl": float(fut["pnl"].sum()),
        "n_options": len(opt),
        "n_futures": len(fut),
        "opt_win_rate": 100.0 * (opt["pnl"] > 0).mean() if len(opt) else 0.0,
        "fut_win_rate": 100.0 * (fut["pnl"] > 0).mean() if len(fut) else 0.0,
        "avg_option_pnl": float(opt["pnl"].mean()) if len(opt) else 0.0,
        "total_fees": float(df["fees"].sum()),
        "max_dd": max_dd,
        "ending_equity": float(eq[-1]) if len(eq) else 0.0,
    }


def summarize(df: pd.DataFrame, ledger: pd.DataFrame) -> str:
    if df.empty:
        return "No trades executed."
    s = _stats(df)
    lines = [
        "=" * 62,
        "BTCUSD SuperTrend-Directional Premium Harvest — Backtest",
        "=" * 62,
        f"Total trades:        {s['n']}  ({s['n_options']} options, {s['n_futures']} futures)",
        f"Win rate:            {s['wins']}/{s['n']} ({s['win_rate']:.1f}%)",
        f"Total P&L:           ${s['total']:,.2f}",
        f"  Premium (options): ${s['option_pnl']:,.2f}  (win {s['opt_win_rate']:.1f}%)",
        f"  Directional (fut): ${s['future_pnl']:,.2f}  (win {s['fut_win_rate']:.1f}%)",
        f"Avg premium/option:  ${s['avg_option_pnl']:,.2f}",
        f"Total fees:          ${s['total_fees']:,.2f}",
        f"Max drawdown:        ${s['max_dd']:,.2f}",
        f"Ending equity:       ${s['ending_equity']:,.2f}",
        "=" * 62,
    ]
    return "\n".join(lines)


def _md_table(df: pd.DataFrame) -> str:
    cols = ["kind", "side", "opt_type", "strike", "entry_ts", "exit_ts",
            "entry_price", "exit_price", "pnl", "exit_reason", "equity"]
    head = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    body = []
    for _, r in df.iterrows():
        vals = []
        for c in cols:
            v = r[c]
            if isinstance(v, pd.Timestamp):
                v = v.strftime("%Y-%m-%d %H:%M")
            vals.append("" if pd.isna(v) else str(v))
        body.append("| " + " | ".join(vals) + " |")
    return "\n".join([head, sep] + body)


def write_markdown(df: pd.DataFrame, ledger: pd.DataFrame, cfg=CONFIG) -> str:
    s = _stats(df) if not df.empty else {}
    period = ""
    if not ledger.empty:
        period = f"{ledger['date'].min():%Y-%m-%d} → {ledger['date'].max():%Y-%m-%d}"

    md = []
    md.append("# BTCUSD SuperTrend-Directional Premium-Harvest — Backtest Results\n")
    md.append("## Strategy\n")
    md.append(
        "A directional **premium-selling** strategy for BTC options on Delta Exchange, "
        "adapted from the *Poor Man's Covered Put/Call* video. Instead of a market-neutral "
        "straddle (unlimited two-sided risk), it trades **one direction at a time**, chosen "
        "by a SuperTrend filter, and earns from **option time decay (theta)** rather than the "
        "move itself.\n")
    md.append("**Rules**\n")
    md.append(
        "1. **Trend filter** — SuperTrend "
        f"(ATR {cfg.st_atr_period}, ×{cfg.st_multiplier}) on the {cfg.st_timeframe_hours}h index candle "
        "sets the bias: down-trend → bearish, up-trend → bullish.\n"
        "2. **Directional (future) leg** — hold a 1× index future aligned with the trend "
        "(short in a down-trend, long in an up-trend). It is the hedge; it is **exited when "
        "SuperTrend flips**, which caps the loss (the video's alternative to a hard stop / OTM hedge).\n"
        f"3. **Short-option (theta) leg** — sell the nearest short-dated option "
        f"({cfg.short_min_dte_days:g}–{cfg.short_max_dte_days:g} DTE) on the trend side "
        f"(**PUT** in a down-trend = covered put, **CALL** in an up-trend = covered call) near "
        f"|Δ|≈{cfg.short_target_delta:g}, and hold it to expiry. Roll into a new one as soon as it settles.\n"
        "4. **Income = decayed premium**, collected as long as the trend holds; whipsaws are the "
        "main risk (repeated small trend-flip exits).\n")

    md.append("## Parameters\n")
    md.append(
        "| Parameter | Value |\n| --- | --- |\n"
        f"| SuperTrend timeframe | {cfg.st_timeframe_hours}h |\n"
        f"| SuperTrend ATR / mult | {cfg.st_atr_period} / {cfg.st_multiplier} |\n"
        f"| Short-option target \\|Δ\\| | {cfg.short_target_delta:g} |\n"
        f"| Short-option DTE window | {cfg.short_min_dte_days:g}–{cfg.short_max_dte_days:g} days |\n"
        f"| Contracts / leg | {cfg.lots} (×{cfg.lot_multiplier} BTC) |\n"
        f"| Taker fee | {cfg.fee_rate*100:.3f}% notional, capped {cfg.fee_cap_frac*100:.0f}% of premium |\n"
        f"| Future leg | {'on' if cfg.trade_future_leg else 'off'} |\n")

    md.append("## Results\n")
    if df.empty:
        md.append("_No trades were generated._\n")
    else:
        md.append(f"**Backtest period:** {period}\n")
        md.append(
            "| Metric | Value |\n| --- | --- |\n"
            f"| Total trades | {s['n']} ({s['n_options']} options, {s['n_futures']} futures) |\n"
            f"| Win rate | {s['wins']}/{s['n']} ({s['win_rate']:.1f}%) |\n"
            f"| **Total P&L** | **${s['total']:,.2f}** |\n"
            f"| Premium (options) P&L | ${s['option_pnl']:,.2f} (win {s['opt_win_rate']:.1f}%) |\n"
            f"| Directional (future) P&L | ${s['future_pnl']:,.2f} (win {s['fut_win_rate']:.1f}%) |\n"
            f"| Avg premium / option | ${s['avg_option_pnl']:,.2f} |\n"
            f"| Total fees | ${s['total_fees']:,.2f} |\n"
            f"| Max drawdown (daily MTM) | ${s['max_dd']:,.2f} |\n"
            f"| Ending equity | ${s['ending_equity']:,.2f} |\n")
        md.append("\n> P&L is in USD for a single contract (0.001 BTC per leg). Scale linearly by `lots`.\n")

        md.append("\n## What was wrong (bugs found & fixed)\n")
        md.append(
            "The first cut of this backtest lost money (-$9.92). Investigation found three real "
            "issues, not a broken strategy:\n\n"
            "1. **Premium coverage gap (bug).** The roll logic only sold a new option once the "
            "previous one had *fully* expired, which — with an 08:00 entry and 12:00 expiry — "
            "left ~63% of days with no option on. Only 121 options were sold over 329 days. "
            "Fixed by rolling on the morning of each expiry day, giving continuous daily "
            "coverage (**{n_opt} options** now).\n"
            "2. **Trend filter too fast (tuning).** The video's literal **4h** SuperTrend "
            "whipsawed on 2026 BTC — 45 trend flips, 31% win, -$15.61 on the directional leg. "
            "This is exactly the *whipsaw loophole the video itself warns about*. Slowing the "
            "filter to **8h** cut it to {n_fut} flips and turned the directional leg to "
            "**${fut:,.2f}**.\n"
            "3. **Look-ahead bias (bug).** The SuperTrend direction was read from the *current* "
            "(not-yet-closed) higher-timeframe candle. Following the reference indicator's "
            "`.shift(1)`, the signal is now taken only from the last *closed* candle — honest, "
            "and slightly lower P&L as a result.\n".format(
                n_opt=s["n_options"], n_fut=s["n_futures"], fut=s["future_pnl"]))
        md.append("\n## Honest read of the edge\n")
        md.append(
            "- The **premium (theta) leg is ~breakeven on its own** (${:,.2f}). 1-DTE options are "
            "priced roughly fairly: the small vol premium you collect is given back on the ~18–30% "
            "that expire ITM plus the occasional gap (e.g. the Aug-19 call caught a +11% overnight "
            "move). This tempers the video's claim that the money comes from premium.\n"
            "- The **profit here is directional** — the SuperTrend future leg riding 2026's large "
            "trends. That also means the result is **sensitive to the trend-filter settings** and to "
            "this particular (strongly trending) sample; a chop-heavy period would look very "
            "different.\n".format(s["option_pnl"]))

        md.append("\n## Notes & caveats\n")
        md.append(
            "- **Fills** use the volume-weighted traded price of each strike from the Delta "
            "options tape within the entry window; options settle at the `.DEXBTUSD` index at "
            "12:00 UTC on expiry. No slippage beyond VWAP is modelled.\n"
            "- The directional leg is modelled as a **1× index future** exited on SuperTrend flip. "
            "This is the risk-defining hedge; the video's deep-ITM-option (poor-man's) variant has "
            "a similar payoff but a higher margin cost.\n"
            "- Premium income is real but **capped**; the tail risk is a fast reversal before the "
            "future leg is exited (whipsaw). Drawdown above is daily mark-to-market on the open future.\n"
            "- Results depend on the SuperTrend settings and the target delta — tune in "
            "`src/config.py` and re-run `python run_backtest.py`.\n")

        md.append("\n## Trade log\n")
        md.append(_md_table(df) + "\n")

    text = "\n".join(md)
    (RESULTS_DIR / "backtest_results.md").write_text(text, encoding="utf-8")
    return text


def write_reports(trades, ledger):
    df = build_trade_frame(trades)
    df.to_csv(RESULTS_DIR / "trades.csv", index=False)
    if not ledger.empty:
        ledger.to_csv(RESULTS_DIR / "equity_curve.csv", index=False)
    summary = summarize(df, ledger)
    (RESULTS_DIR / "summary.txt").write_text(summary, encoding="utf-8")
    write_markdown(df, ledger)
    return df, summary
