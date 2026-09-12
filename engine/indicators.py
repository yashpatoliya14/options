from __future__ import annotations

import math

import pandas as pd


def resolution_seconds(resolution: str) -> int:
    value = str(resolution).strip().lower()
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("d"):
        return int(value[:-1]) * 86400
    return int(float(value))


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """
    Canonical Wilder ATR (RMA): SMA seed over the first `period` true ranges,
    then atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period.

    This matches TradingView ta.rma exactly (which also SMA-seeds), unlike a
    plain ewm(alpha=1/period) which weights the whole history from bar 0 and
    diverges for the first ~3x period bars.
    """
    period = max(int(period), 1)
    tr = true_range(high, low, close)
    values = tr.tolist()
    out = [float("nan")] * len(values)
    running = 0.0
    prev: float | None = None
    for i, value in enumerate(values):
        if value is None or (isinstance(value, float) and value != value):
            value = 0.0
        if prev is None:
            running += value
            if i + 1 >= period:
                out[i] = running / period
                prev = out[i]
        else:
            prev = (prev * (period - 1) + value) / period
            out[i] = prev
    return pd.Series(out, index=tr.index)


def sma_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    period = max(int(period), 1)
    return true_range(high, low, close).rolling(period).mean()


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 15,
    multiplier: float = 1.5,
) -> pd.DataFrame:
    """
    TradingView-style Supertrend.

    ATR is Wilder-smoothed. Final bands ratchet. Trend stays until close
    crosses the Supertrend line. Column `trend` is +1 (bull) or -1 (bear).
    """
    atr = wilder_atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = pd.Series(1, index=close.index, dtype=int)
    st_line = pd.Series(index=close.index, dtype=float)

    for i in range(len(close)):
        if i == 0 or pd.isna(atr.iloc[i]):
            final_upper.iloc[i] = basic_upper.iloc[i]
            final_lower.iloc[i] = basic_lower.iloc[i]
            st_line.iloc[i] = final_upper.iloc[i] if pd.notna(final_upper.iloc[i]) else hl2.iloc[i]
            continue

        prev_fu = final_upper.iloc[i - 1]
        prev_fl = final_lower.iloc[i - 1]
        prev_close = close.iloc[i - 1]

        if pd.isna(prev_fu) or basic_upper.iloc[i] < prev_fu or prev_close > prev_fu:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = prev_fu

        if pd.isna(prev_fl) or basic_lower.iloc[i] > prev_fl or prev_close < prev_fl:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = prev_fl

        prev_st = st_line.iloc[i - 1]
        if pd.isna(prev_st) or prev_st == prev_fu:
            if close.iloc[i] <= final_upper.iloc[i]:
                st_line.iloc[i] = final_upper.iloc[i]
                trend.iloc[i] = -1
            else:
                st_line.iloc[i] = final_lower.iloc[i]
                trend.iloc[i] = 1
        else:
            if close.iloc[i] >= final_lower.iloc[i]:
                st_line.iloc[i] = final_lower.iloc[i]
                trend.iloc[i] = 1
            else:
                st_line.iloc[i] = final_upper.iloc[i]
                trend.iloc[i] = -1

    return pd.DataFrame(
        {
            "atr": atr,
            "supertrend": st_line,
            "trend": trend,
            "final_upper": final_upper,
            "final_lower": final_lower,
        },
        index=close.index,
    )


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=max(int(period), 1), adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    period = max(int(period), 1)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100.0 - (100.0 / (1.0 + rs))


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    period = max(int(period), 1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr = wilder_atr(high, low, close, period)
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))) * 100
    return dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def realized_vol(close: pd.Series, window: int, periods_per_year: int) -> pd.Series:
    close_f = close.astype(float)
    log_ret = (close_f / close_f.shift(1)).map(lambda x: math.log(x) if pd.notna(x) and x > 0 else float("nan"))
    return log_ret.rolling(max(int(window), 2)).std() * (periods_per_year ** 0.5)


def bollinger_width(close: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.Series:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return (2.0 * n_std * std) / mid.replace(0, float("nan"))


def roc(close: pd.Series, period: int = 10) -> pd.Series:
    return close.pct_change(periods=max(int(period), 1))


def macd_histogram(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line - signal_line
