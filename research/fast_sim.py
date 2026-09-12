"""
Fast vectorized research simulator for the BTC Supertrend options strategy.

Purpose: run the thousands of backtests required for staged optimization,
walk-forward training, parameter perturbation, and Monte Carlo without
re-deriving Black-Scholes chains per candle.

Verification policy: results must match the event-driven BacktestRunner
(entry candle, strikes, expiry, exit candle, P&L) before any research
number is produced. See tests/test_fast_sim_parity.py.

Method (mirrors engine/ + backtest/ semantics):
  - Supertrend(15, 1.5) flips on closed 1H candles only
  - EMA-50 trend filter, min_trend_bars=2 confirmation (same as live)
  - Trades entered at the close of the signal candle (bid/ask leg fills)
  - Chain reconstructed per candle via Black-Scholes with a flat IV and a
    1% per-leg half-spread (bid/ask), matching HistoricalDataProvider
  - per-leg bid/ask fills + slippage, notional taker fee 0.125% per leg
  - 0DTE expiry at 12:30 UTC (Delta settlement); signals at 12:00+ UTC use
    next-day expiry, matching engine.get_expiry_type(cutoff 9 -> hour>=12)
  - exits: TP/SL on executable close debit, opposite signal, expiry
"""

from __future__ import annotations

import math

import pandas as pd

from engine.black_scholes import greeks as bs_greeks
from engine.indicators import ema, realized_vol, supertrend as st_indicator, wilder_atr

SETTLE_HOUR_UTC = 12
SETTLE_MIN_UTC = 30      # Delta India settlement 18:00 IST = 12:30 UTC
HALF_SPREAD = 0.01       # 1% per-leg half-spread (BS reconstruction)
SLIPPAGE_PCT = 0.0       # fills already at bid/ask; no extra slippage
TAKER_FEE_PCT = 0.00125  # notional taker fee per leg
CONTRACT_SIZE = 0.001    # BTC per contract


def _strike_step(spot: float) -> float:
    return float(max(round(spot * 0.01 / 50.0) * 50, 50))


def _expiry_ts(date_str: str) -> pd.Timestamp:
    ts = pd.Timestamp(date_str)
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")) + pd.Timedelta(
        hours=SETTLE_HOUR_UTC, minutes=SETTLE_MIN_UTC
    )


def prepare(candles: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicator columns once for the whole dataset."""
    df = candles.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    df = df.sort_values("timestamp").reset_index(drop=True)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    st = st_indicator(high, low, close, period=15, multiplier=1.5)
    df["st_trend"] = st["trend"]
    df["st_line"] = st["supertrend"]
    df["ema50"] = ema(close, 50)
    df["rv24"] = realized_vol(close, 24, 365 * 24)
    df["atr_pct"] = wilder_atr(high, low, close, 15) / close

    # Regime columns for analysis (always computed; used in reports)
    df["roc24"] = close.pct_change(24)
    df["roc72"] = close.pct_change(72)
    df["bbw"] = (close.rolling(20).std() * 4.0) / close.rolling(20).mean()
    df["rv_pctile"] = df["rv24"].rolling(500, min_periods=100).rank(pct=True)
    df["hour"] = df["timestamp"].dt.hour
    return df


def signals_from_frame(df: pd.DataFrame, min_trend_bars: int = 2, ema_filter: bool = True) -> list[dict]:
    """Signal candles = first bar where trend held N bars after a flip."""
    trend = df["st_trend"].tolist()
    close = df["close"].tolist()
    ema50 = df["ema50"].tolist()
    signals: list[dict] = []
    n = len(df)
    for i in range(min_trend_bars, n):
        cur = int(trend[i])
        if cur not in (1, -1):
            continue
        held = all(int(trend[i - k]) == cur for k in range(min_trend_bars))
        if not held:
            continue
        prev = int(trend[i - min_trend_bars])
        if prev == cur:
            continue
        if ema_filter:
            if cur == 1 and close[i] < ema50[i]:
                continue
            if cur == -1 and close[i] > ema50[i]:
                continue
        direction = "bull" if cur == 1 else "bear"
        signals.append(
            {
                "idx": i,
                "timestamp": df["timestamp"].iloc[i],
                "direction": direction,
                "close": float(close[i]),
                "atr_pct": float(df["atr_pct"].iloc[i]),
                "rv": float(df["rv24"].iloc[i]) if pd.notna(df["rv24"].iloc[i]) else None,
            }
        )
    return signals


def _pick_strikes(
    df: pd.DataFrame,
    idx: int,
    direction: str,
    iv: float,
    offset_pct: float,
    expiry_selection: str | None = None,
    cutoff_hour: int = 9,
    target_dte: float = 1.0,
    min_dte: float = 0.0,
    max_dte: float = 14.0,
):
    """Reconstruct the chain at candle close and select strikes like the engine.
    Engine mapping: bull → puts, bear → calls (bear_structure=call_credit)."""
    spot = float(df["close"].iloc[idx])
    ts = pd.Timestamp(df["timestamp"].iloc[idx])
    option_type = "put" if direction == "bull" else "call"

    # Expiry: same-day unless signal at/after the cutoff hour (cutoff_hour mode),
    # or DTE-scored closest to target_dte (nearest_valid_after_signal mode),
    # replicating engine/option_analytics.select_expiry_date.
    candidates = [ts.date(), ts.date() + pd.Timedelta(days=1)]
    expiry_mode = expiry_selection or "cutoff_hour"
    if expiry_mode == "nearest_valid_after_signal":
        scored = []
        for d in candidates:
            t = max((_expiry_ts(d.isoformat()) - ts).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)
            days = t * 365.0
            if days < min_dte - 1e-6 or days > max_dte + 1e-6:
                continue
            scored.append((abs(days - target_dte), d.isoformat()))
        scored.sort()
        expiry_str = scored[0][1] if scored else candidates[0].isoformat()
    else:
        date = ts.date() if ts.hour < cutoff_hour else ts.date() + pd.Timedelta(days=1)
        expiry_str = date.isoformat()
    expiry = _expiry_ts(expiry_str)
    t_years = max((expiry - ts).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)
    if t_years <= 0:
        return None

    step = _strike_step(spot)
    center = round(spot / step) * step
    offsets = range(-20, 21)
    grid = [center + o * step for o in offsets if center + o * step > 0]
    offset_target = spot * (1.0 - offset_pct) if direction == "bull" else spot * (1.0 + offset_pct)

    best = None
    for strike in grid:
        if direction == "bull" and strike > spot:
            continue
        if direction == "bear" and strike < spot:
            continue
        dist = abs(strike - offset_target)
        if best is None or dist < best[1]:
            best = (strike, dist)
    if best is None:
        return None
    short_strike = best[0]
    # Engine geometry: long target = short -+ width, then nearest grid strike
    # strictly below (bull) / above (bear) the short strike.
    long_target = short_strike - (200.0 if direction == "bull" else -200.0)
    candidates = [s for s in grid if (s < short_strike if direction == "bull" else s > short_strike)]
    if not candidates:
        return None
    long_strike = min(candidates, key=lambda s: abs(s - long_target))

    rows = {}
    for label, strike in (("short", short_strike), ("long", long_strike)):
        if strike <= 0:
            return None
        g = bs_greeks(spot, strike, t_years, iv, 0.0, option_type)
        mark = g["price"]
        rows[label] = {
            "strike": strike,
            "bid": max(mark * (1.0 - HALF_SPREAD), 0.0),
            "ask": mark * (1.0 + HALF_SPREAD),
            "mark": mark,
            "delta": g["delta"],
            "gamma": g["gamma"],
            "theta": g["theta"],
            "vega": g["vega"],
            "iv": iv,
        }
    rows["t_years"] = t_years
    rows["spot"] = spot
    rows["expiry"] = expiry_str
    return rows


def _marks_at(
    df: pd.DataFrame,
    idx: int,
    short_strike: float,
    long_strike: float,
    iv: float,
    option_type: str = "put",
    expiry_str: str | None = None,
) -> dict | None:
    """Re-mark the position legs. Uses the position's own expiry when given
    (next-day positions must not be marked against a same-day expiry)."""
    spot = float(df["close"].iloc[idx])
    ts = pd.Timestamp(df["timestamp"].iloc[idx])
    exp_date = pd.Timestamp(expiry_str).date() if expiry_str else pd.Timestamp(df["timestamp"].iloc[idx]).date()
    expiry = _expiry_ts(exp_date.isoformat())
    t_years = (expiry - ts).total_seconds() / (365.0 * 24.0 * 3600.0)
    if t_years <= 0:
        return None
    out = {"spot": spot, "t_years": t_years}
    for label, strike in (("short", short_strike), ("long", long_strike)):
        g = bs_greeks(spot, strike, t_years, iv, 0.0, option_type)
        mark = g["price"]
        out[label] = {
            "strike": strike,
            "bid": max(mark * (1.0 - HALF_SPREAD), 0.0),
            "ask": mark * (1.0 + HALF_SPREAD),
            "mark": mark,
            "delta": g["delta"],
            "theta": g["theta"],
            "vega": g["vega"],
            "iv": iv,
        }
    return out


def run_backtest(
    df: pd.DataFrame,
    signals: list[dict],
    tp_pct: float = 0.60,
    sl_pct: float = 1.50,
    cooldown_bars: int = 6,
    exit_on_opposite: bool = False,
    iv: float = 0.55,
    offset_pct: float = 0.015,
    qty: int = 1,
    signal_filter=None,
    early_exit_minutes: int = 240,
    max_hold_bars: int = 0,
    expiry_selection: str | None = None,
    cutoff_hour: int = 9,
    target_dte: float = 1.0,
    min_dte: float = 0.0,
    max_dte: float = 14.0,
) -> dict:
    """
    Trade the signal list bar by bar. signal_filter maps a signal dict to
    bool (allow) and is applied before any state is touched.
    """
    n = len(df)
    ts_list = pd.to_datetime(df["timestamp"], utc=True).tolist()
    trades: list[dict] = []
    position: dict | None = None
    last_close_idx: int | None = None

    filtered = signals if signal_filter is None else [s for s in signals if signal_filter(s)]

    for i in range(n):
        now = ts_list[i]

        # ---- manage open position ----
        if position is not None:
            marks = _marks_at(
                df, i, position["short_strike"], position["long_strike"], iv,
                option_type=position["option_type"], expiry_str=position["expiry"],
            )
            if marks is not None:
                # Executable close debit: ask(short) - bid(long), with slippage.
                close_debit = (
                    marks["short"]["ask"] * (1.0 + SLIPPAGE_PCT)
                    - marks["long"]["bid"] * (1.0 - SLIPPAGE_PCT)
                )
                entry = position["entry_credit"]
                reason = None
                if entry > 0:
                    if entry - close_debit >= entry * tp_pct:
                        reason = "profit_target"
                    elif close_debit >= entry * sl_pct:
                        reason = "stop_loss"
                else:
                    debit = abs(entry)
                    max_profit = max(position["width"] - debit, 0.0)
                    unrealized = entry - close_debit
                    if max_profit > 0 and unrealized >= max_profit * tp_pct:
                        reason = "profit_target"
                    elif unrealized <= -debit * sl_pct:
                        reason = "stop_loss"
                held_min = (now - position["entry_time"]).total_seconds() / 60.0
                if reason is None and position["expiry_type"] == "same_day":
                    if held_min >= early_exit_minutes:
                        reason = "time_exit"
                if reason is None and max_hold_bars > 0:
                    if i - position["entry_idx"] >= max_hold_bars:
                        reason = "time_exit"
                if reason is None:
                    # expiry settlement of the POSITION's own expiry
                    exp_ts = _expiry_ts(pd.Timestamp(position["expiry"]).date().isoformat())
                    if now >= exp_ts:
                        reason = "expired"
                if reason is not None:
                    _close(trades, position, i, now, marks, close_debit, reason, qty)
                    position = None
                    last_close_idx = i

        # ---- entries ----
        sig = _signal_at(filtered, i)
        if sig is None:
            continue
        if position is not None:
            if exit_on_opposite and sig["direction"] != position["direction"]:
                marks = _marks_at(
                    df, i, position["short_strike"], position["long_strike"], iv,
                    option_type=position["option_type"], expiry_str=position["expiry"],
                )
                if marks is not None:
                    close_debit = (
                        marks["short"]["ask"] * (1.0 + SLIPPAGE_PCT)
                        - marks["long"]["bid"] * (1.0 - SLIPPAGE_PCT)
                    )
                    _close(trades, position, i, now, marks, close_debit, "signal_cut", qty)
                    position = None
                    last_close_idx = i
            else:
                continue
        if last_close_idx is not None and i - last_close_idx < cooldown_bars:
            continue
        if position is not None:
            continue

        picked = _pick_strikes(
            df, i, sig["direction"], iv, offset_pct,
            expiry_selection=expiry_selection,
            cutoff_hour=cutoff_hour,
            target_dte=target_dte,
            min_dte=min_dte,
            max_dte=max_dte,
        )
        if picked is None:
            continue
        short_fill = picked["short"]["bid"] * (1.0 - SLIPPAGE_PCT)
        long_fill = picked["long"]["ask"] * (1.0 + SLIPPAGE_PCT)
        credit = short_fill - long_fill
        if credit <= 0:
            continue
        entry_fee = _leg_fee(short_fill, qty) + _leg_fee(long_fill, qty)
        expiry_type = (
            "same_day" if str(picked["expiry"]) == pd.Timestamp(ts_list[i]).date().isoformat()
            else "next_day"
        )
        position = {
            "direction": sig["direction"],
            "signal_type": "buy" if sig["direction"] == "bull" else "sell",
            "option_type": "put" if sig["direction"] == "bull" else "call",
            "entry_idx": i,
            "entry_time": now,
            "short_strike": picked["short"]["strike"],
            "long_strike": picked["long"]["strike"],
            "width": abs(picked["short"]["strike"] - picked["long"]["strike"]),
            "entry_credit": credit,
            "entry_mid": picked["short"]["mark"] - picked["long"]["mark"],
            "entry_fee": entry_fee,
            "qty": qty,
            "expiry": picked["expiry"],
            "expiry_type": expiry_type,
            "entry_spot": picked["spot"],
            "entry_short_delta": picked["short"]["delta"],
            "entry_long_delta": picked["long"]["delta"],
            "entry_theta": -picked["short"]["theta"] + picked["long"]["theta"],
            "entry_vega": -picked["short"]["vega"] + picked["long"]["vega"],
            "dte": picked["t_years"] * 365.0,
            "atr_pct": sig.get("atr_pct"),
            "rv": sig.get("rv"),
            "hour": pd.Timestamp(now).hour,
        }

    if position is not None:
        marks = _marks_at(
            df, n - 1, position["short_strike"], position["long_strike"], iv,
            option_type=position["option_type"], expiry_str=position["expiry"],
        )
        if marks is not None:
            close_debit = (
                marks["short"]["ask"] * (1.0 + SLIPPAGE_PCT)
                - marks["long"]["bid"] * (1.0 - SLIPPAGE_PCT)
            )
            _close(trades, position, n - 1, ts_list[n - 1], marks, close_debit, "expired", qty)

    return {"trades": trades, "df": df}


def _signal_at(signals: list[dict], idx: int) -> dict | None:
    lo, hi = 0, len(signals) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if signals[mid]["idx"] == idx:
            return signals[mid]
        if signals[mid]["idx"] < idx:
            lo = mid + 1
        else:
            hi = mid - 1
    return None


def _leg_fee(fill_price: float, qty: int) -> float:
    return abs(fill_price) * CONTRACT_SIZE * qty * TAKER_FEE_PCT


def _close(trades, position, idx, now, marks, close_debit, reason, qty):
    exit_fee = _leg_fee(marks["short"]["ask"], qty) + _leg_fee(marks["long"]["bid"], qty)
    pnl = (position["entry_credit"] - close_debit) * position["qty"] * CONTRACT_SIZE - position["entry_fee"] - exit_fee
    trades.append(
        {
            **position,
            "exit_idx": idx,
            "exit_time": now,
            "exit_debit": close_debit,
            "exit_reason": reason,
            "pnl": pnl,
            "fees": position["entry_fee"] + exit_fee,
            "exit_spot": marks["spot"],
            "exit_mid": marks["short"]["mark"] - marks["long"]["mark"],
            "duration_min": (now - position["entry_time"]).total_seconds() / 60.0,
        }
    )


def summarize(trades: list[dict], start_ts, end_ts, capital: float = 10000.0) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    days = max((pd.Timestamp(end_ts) - pd.Timestamp(start_ts)).total_seconds() / 86400.0, 1.0)
    years = days / 365.25

    equity = capital
    peak = capital
    max_dd = 0.0
    max_dd_pct = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = equity - peak
        max_dd = min(max_dd, dd)
        if peak > 0:
            max_dd_pct = min(max_dd_pct, dd / peak * 100.0)

    gp = sum(wins)
    gl = abs(sum(losses))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    win_rate = len(wins) / n if n else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    expectancy = (win_rate * avg_win - (1 - win_rate) * avg_loss) if n else 0.0
    trades_per_year = n / years if years > 0 else 0.0

    sharpe = 0.0
    if n > 1:
        mean = sum(pnls) / n
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var)
        if std > 0:
            sharpe = mean / std * math.sqrt(max(trades_per_year, 1.0))

    cagr = 0.0
    end_equity = capital + sum(pnls)
    if years > 0 and capital > 0 and end_equity > 0:
        cagr = ((end_equity / capital) ** (1.0 / years) - 1.0) * 100.0

    def sub(rows, key):
        vals = [t["pnl"] for t in rows]
        w = [p for p in vals if p > 0]
        l = [p for p in vals if p < 0]
        gp2, gl2 = sum(w), abs(sum(l))
        return {
            "trades": len(vals),
            "win_rate": len(w) / len(vals) if vals else 0.0,
            "net": sum(vals),
            "pf": gp2 / gl2 if gl2 > 0 else (float("inf") if gp2 > 0 else 0.0),
            "avg": sum(vals) / len(vals) if vals else 0.0,
        }

    return {
        "trades": n,
        "win_rate": win_rate,
        "net": sum(pnls),
        "pf": pf,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": -avg_loss,
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe,
        "cagr": cagr,
        "total_fees": sum(t["fees"] for t in trades),
        "days": days,
        "bull": sub([t for t in trades if t["signal_type"] == "buy"], "pnl"),
        "bear": sub([t for t in trades if t["signal_type"] == "sell"], "pnl"),
    }
