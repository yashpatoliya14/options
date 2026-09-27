"""SuperTrend indicator + trend filter.

Core logic follows the reference implementation supplied by the user (ccxt +
pandas_ta style): ATR-scaled bands with the classic trailing-band recursion,
signals from close-vs-band crossings, and a `.shift(1)` to remove look-ahead
bias. The heavy plotting / ccxt-fetch helpers from the reference are kept under
`__main__` with lazy imports so importing this module for the backtest never
requires ccxt / pandas_ta / mplfinance.

`TrendFilter` wraps the same computation for the backtest: it resamples the 1h
index to the configured higher timeframe, computes the (look-ahead-free) signal,
and serves the direction as-of any timestamp.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 15) -> pd.Series:
    """Average True Range with Wilder's smoothing (pandas_ta `atr` default)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def supertrend(df: pd.DataFrame, atr_period: int = 15, atr_multiplier: float = 3.0) -> pd.DataFrame:
    """Add `atr`, `upperband`, `lowerband` using the trailing-band recursion.

    Formula: mid = (high+low)/2 ; basic bands = mid +/- multiplier*ATR, then the
    bands are ratcheted so they only tighten toward price (classic SuperTrend).
    """
    df = df.copy()
    mid = (df["high"] + df["low"]) / 2.0
    df["atr"] = wilder_atr(df["high"], df["low"], df["close"], period=atr_period)
    df.dropna(inplace=True)

    basic_upper = (mid + atr_multiplier * df["atr"]).to_numpy()
    basic_lower = (mid - atr_multiplier * df["atr"]).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    upper = np.empty(n)
    lower = np.empty(n)
    if n:
        upper[0] = basic_upper[0]
        lower[0] = basic_lower[0]
    for i in range(1, n):
        upper[i] = basic_upper[i] if (basic_upper[i] < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = basic_lower[i] if (basic_lower[i] > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]

    df["upperband"] = upper
    df["lowerband"] = lower
    return df


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """+1 (long) when close breaks above the upper band, -1 (short) below the
    lower band, else carry the prior signal. `.shift(1)` removes look-ahead bias
    so a bar's signal is only ever acted on from the *next* bar onward.
    """
    df = df.copy()
    close = df["close"].to_numpy()
    upper = df["upperband"].to_numpy()
    lower = df["lowerband"].to_numpy()

    signals = [0]
    for i in range(1, len(df)):
        if close[i] > upper[i]:
            signals.append(1)
        elif close[i] < lower[i]:
            signals.append(-1)
        else:
            signals.append(signals[i - 1])

    df["signals"] = signals
    df["signals"] = df["signals"].shift(1)   # remove look-ahead bias
    return df


class TrendFilter:
    """Serve the (look-ahead-free) SuperTrend direction as-of any timestamp."""

    def __init__(self, frame: pd.DataFrame):
        # indexed by higher-timeframe candle OPEN time; `signals` already shifted(1)
        self._f = frame.sort_index()
        self._sig = self._f["signals"]

    @classmethod
    def build(cls, candles: pd.DataFrame, cfg) -> "TrendFilter":
        tf = candles[["open", "high", "low", "close"]].resample(
            f"{cfg.st_timeframe_hours}h").agg({
                "open": "first", "high": "max", "low": "min", "close": "last",
            }).dropna()
        st = supertrend(tf, cfg.st_atr_period, cfg.st_multiplier)
        st = generate_signals(st)
        return cls(st[["signals", "upperband", "lowerband"]])

    def _pos_at(self, ts: pd.Timestamp) -> int:
        idx = self._sig.index
        return idx.searchsorted(pd.Timestamp(ts), side="right") - 1

    def direction_at(self, ts: pd.Timestamp) -> int | None:
        """+1 (up) / -1 (down) from the last *closed* higher-timeframe candle.

        Because the signal is shifted by one candle, reading the candle that is
        active at `ts` reflects only information available before `ts`.
        """
        pos = self._pos_at(ts)
        if pos < 0:
            return None
        v = self._sig.iloc[pos]
        if pd.isna(v) or v == 0:
            return None
        return int(v)

    def stop_level_at(self, ts: pd.Timestamp) -> float | None:
        """SuperTrend trailing-line level to use as the future's stop-loss.

        In an up-trend the line is the lowerband (stop below price); in a
        down-trend it is the upperband (stop above price). Read from the same
        last-closed candle as `direction_at`, so it is the level the SuperTrend
        would flip on.
        """
        pos = self._pos_at(ts)
        if pos < 0:
            return None
        v = self._sig.iloc[pos]
        if pd.isna(v) or v == 0:
            return None
        band = "lowerband" if v > 0 else "upperband"
        return float(self._f[band].iloc[pos])


# --------------------------------------------------------------------------
# Standalone reference tooling (ccxt fetch + plot + performance). These mirror
# the supplied reference script; imports are lazy so the backtest never needs
# ccxt / mplfinance / matplotlib installed.
# --------------------------------------------------------------------------
def fetch_asset_data(symbol: str, start_date: str, interval: str, exchange) -> pd.DataFrame:
    since = exchange.parse8601(start_date)
    ohlcv = exchange.fetch_ohlcv(symbol, interval, since=since)
    df = pd.DataFrame(ohlcv, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"], unit="ms")
    df.set_index("date", inplace=True)
    df.drop(df.index[-1], inplace=True)          # drop the live (incomplete) bar
    return df


def create_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Mark buy/sell entry prices at signal reversals (for plotting)."""
    df = df.copy()
    df.loc[df["signals"] == 1, "upperband"] = np.nan
    df.loc[df["signals"] == -1, "lowerband"] = np.nan
    sig = df["signals"].to_numpy()
    close = df["close"].to_numpy()
    buy, sell = [np.nan], [np.nan]
    for i in range(1, len(df)):
        if sig[i] == 1 and sig[i] != sig[i - 1]:
            buy.append(close[i]); sell.append(np.nan)
        elif sig[i] == -1 and sig[i] != sig[i - 1]:
            sell.append(close[i]); buy.append(np.nan)
        else:
            buy.append(np.nan); sell.append(np.nan)
    df["buy_positions"] = buy
    df["sell_positions"] = sell
    return df


def strategy_performance(df: pd.DataFrame, capital: float = 100, leverage: float = 1) -> pd.DataFrame:
    """Bar-by-bar equity for the pure SuperTrend long/short flip strategy."""
    df = df.copy()
    balance = capital
    investment = capital
    peak = capital
    max_dd = 0.0
    max_dd_pct = 0.0
    balances, pnls, invs = [capital], [0.0], [capital]
    for i in range(1, len(df)):
        row = df.iloc[i]
        if row["signals"] == 1:
            pl = ((row["close"] - row["open"]) / row["open"]) * investment * leverage
        elif row["signals"] == -1:
            pl = ((row["open"] - row["close"]) / row["close"]) * investment * leverage
        else:
            pl = 0.0
        if row["signals"] != df.iloc[i - 1]["signals"]:
            investment = balance
        balance += pl
        invs.append(investment)
        balances.append(balance)
        pnls.append(pl)
        dd = balance - peak
        if dd < max_dd:
            max_dd = dd
            max_dd_pct = (max_dd / peak) * 100
        peak = max(peak, balance)
    df["investment"] = invs
    df["cumulative_balance"] = balances
    df["pl"] = pnls
    df["cumPL"] = df["pl"].cumsum()
    print("Overall P/L: {:.2f}%".format((balances[-1] - capital) * 100 / capital))
    print("Overall P/L: {:.2f}".format(balances[-1] - capital))
    print("Min balance: {:.2f}".format(min(balances)))
    print("Max balance: {:.2f}".format(max(balances)))
    print("Maximum Drawdown: {:.2f}".format(max_dd))
    print("Maximum Drawdown %: {:.2f}%".format(max_dd_pct))
    return df


def plot_data(df: pd.DataFrame, symbol: str) -> None:
    import mplfinance as mpf
    apd = [
        mpf.make_addplot(df["lowerband"], label="lowerband", color="green"),
        mpf.make_addplot(df["upperband"], label="upperband", color="red"),
        mpf.make_addplot(df["buy_positions"], type="scatter", marker="^",
                         label="Buy", markersize=80, color="#2cf651"),
        mpf.make_addplot(df["sell_positions"], type="scatter", marker="v",
                         label="Sell", markersize=80, color="#f50100"),
    ]
    fills = [
        dict(y1=df["close"].values, y2=df["lowerband"].values, panel=0, alpha=0.3, color="#CCFFCC"),
        dict(y1=df["close"].values, y2=df["upperband"].values, panel=0, alpha=0.3, color="#FFCCCC"),
    ]
    mpf.plot(df, addplot=apd, type="candle", volume=True, style="charles",
             xrotation=20, title=f"{symbol} Supertrend Plot", fill_between=fills)


def plot_performance_curve(df: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt
    plt.plot(df["cumulative_balance"], label="Strategy")
    plt.title("Performance Curve"); plt.xlabel("Date"); plt.ylabel("Balance")
    plt.xticks(rotation=70); plt.legend(); plt.show()


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    import ccxt

    symbol, start_date, interval = "BTC/USDT", "2022-12-1", "4h"
    exchange = ccxt.binance()
    data = fetch_asset_data(symbol=symbol, start_date=start_date, interval=interval, exchange=exchange)

    st = supertrend(data, atr_period=15, atr_multiplier=3)
    st = generate_signals(st)
    st = create_positions(st)
    st = strategy_performance(st, capital=100, leverage=1)
    print(st)
    plot_data(st, symbol=symbol)
    plot_performance_curve(st)
