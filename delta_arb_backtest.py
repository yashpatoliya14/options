"""
Delta Exchange - Put-Call Parity Arbitrage Backtester
=======================================================
Detects and backtests synthetic arbitrage opportunities on Delta Exchange:
  - Conversion   : Buy Put + Buy Future + Sell Call   (same strike, same expiry)
  - Reversal     : Sell Put + Sell Future + Buy Call  (same strike, same expiry)
  - Box Spread   : Buy Call(K1)+Sell Call(K2)+Sell Put(K1)+Buy Put(K2)

Theory:
  Synthetic Future = Call - Put + Strike
  If |Synthetic Future - Actual Future| > total_fees -> arbitrage exists

IMPORTANT: This script needs internet access to api.delta.exchange.
Run it on your own machine / VPS where that domain is reachable
(this sandbox cannot reach it).

Usage:
    pip install requests pandas numpy --break-system-packages
    python3 delta_arb_backtest.py
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

BASE_URL = "https://api.delta.exchange/v2"

# ----------------------------------------------------------------------
# 1. DATA FETCHING
# ----------------------------------------------------------------------

def get_all_products():
    """Fetch all products (options, futures) listed on Delta Exchange."""
    resp = requests.get(f"{BASE_URL}/products", params={"contract_types": "call_options,put_options,futures,perpetual_futures"})
    resp.raise_for_status()
    return resp.json()["result"]


def filter_btc_chain(products, underlying="BTC"):
    """Split products into calls, puts, futures for a given underlying."""
    calls, puts, futures = [], [], []
    for p in products:
        if p.get("underlying_asset", {}).get("symbol") != underlying:
            continue
        ctype = p["contract_type"]
        if ctype == "call_options":
            calls.append(p)
        elif ctype == "put_options":
            puts.append(p)
        elif ctype in ("futures", "perpetual_futures"):
            futures.append(p)
    return calls, puts, futures


def get_candles(symbol, resolution="5m", start=None, end=None):
    """Fetch historical OHLC candle data for a symbol."""
    params = {
        "symbol": symbol,
        "resolution": resolution,
        "start": int(start.timestamp()),
        "end": int(end.timestamp()),
    }
    resp = requests.get(f"{BASE_URL}/history/candles", params=params)
    resp.raise_for_status()
    data = resp.json()["result"]
    df = pd.DataFrame(data)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.sort_values("time").reset_index(drop=True)
    return df


# ----------------------------------------------------------------------
# 2. FEE MODEL  (update these to Delta's current published fee schedule)
# ----------------------------------------------------------------------

TAKER_FEE_OPTION = 0.0003     # 3 bps of underlying (approx, verify current rate)
TAKER_FEE_FUTURE = 0.0005     # 5 bps of notional (approx, verify current rate)
SETTLEMENT_FEE   = 0.00015    # applied at expiry on ITM legs


def leg_cost(price, fee_rate, notional):
    return notional * fee_rate


# ----------------------------------------------------------------------
# 3. ARBITRAGE DETECTION LOGIC
# ----------------------------------------------------------------------

def synthetic_future_price(call_price, put_price, strike):
    """Put-Call Parity: Synthetic Future = Call - Put + Strike"""
    return call_price - put_price + strike


def detect_conversion_reversal(call_price, put_price, strike, future_price,
                                lot_size=1, notional=1):
    """
    Returns dict with edge details for Conversion and Reversal setups.
    Conversion: synthetic future is CHEAPER than actual future -> buy synthetic, sell actual
    Reversal:   synthetic future is DEARER than actual future  -> sell synthetic, buy actual
    """
    synth = synthetic_future_price(call_price, put_price, strike)
    gap = future_price - synth   # positive => actual future overpriced vs synthetic

    # total fees: 2 option legs + 1 future leg (approx, using notional*fee_rate)
    total_fee = (2 * leg_cost(0, TAKER_FEE_OPTION, notional) +
                 leg_cost(0, TAKER_FEE_FUTURE, notional))

    net_edge = abs(gap) - total_fee

    if gap > 0:
        strategy = "Conversion (Buy Call+Future synth cheap: Buy Put, Buy Future, Sell Call)"
    else:
        strategy = "Reversal (synthetic overpriced: Sell Put, Sell Future, Buy Call)"

    return {
        "synthetic_future": synth,
        "actual_future": future_price,
        "raw_gap": gap,
        "total_fee_estimate": total_fee,
        "net_edge": net_edge,
        "tradable": net_edge > 0,
        "strategy": strategy,
    }


def detect_box_spread(call_k1, call_k2, put_k1, put_k2, k1, k2, notional=1):
    """
    Box spread: Buy Call(K1) + Sell Call(K2) + Sell Put(K1) + Buy Put(K2)
    Theoretical fixed payoff at expiry = (K2 - K1)
    Cost today = call_k1 - call_k2 + put_k2 - put_k1
    If cost < (K2-K1) - fees  => arbitrage (buy box, guaranteed profit at expiry)
    """
    cost_today = (call_k1 - call_k2) + (put_k2 - put_k1)
    fixed_payoff = (k2 - k1)
    total_fee = 4 * leg_cost(0, TAKER_FEE_OPTION, notional)
    net_edge = fixed_payoff - cost_today - total_fee

    return {
        "cost_today": cost_today,
        "fixed_payoff_at_expiry": fixed_payoff,
        "total_fee_estimate": total_fee,
        "net_edge": net_edge,
        "tradable": net_edge > 0,
    }


# ----------------------------------------------------------------------
# 4. BACKTEST ENGINE
# ----------------------------------------------------------------------

def backtest_conversion_reversal(call_df, put_df, future_df, strike, notional=1):
    """
    Merge candle data on timestamp, run detection at each bar (using close price),
    log every tradable signal, and compute cumulative P&L assuming instant fill
    at close price and full convergence at expiry (idealized fill assumption).
    """
    merged = call_df[["time", "close"]].rename(columns={"close": "call"}).merge(
        put_df[["time", "close"]].rename(columns={"close": "put"}), on="time"
    ).merge(
        future_df[["time", "close"]].rename(columns={"close": "future"}), on="time"
    )

    results = []
    for _, row in merged.iterrows():
        r = detect_conversion_reversal(row["call"], row["put"], strike, row["future"], notional=notional)
        r["time"] = row["time"]
        results.append(r)

    res_df = pd.DataFrame(results)
    trades = res_df[res_df["tradable"]].copy()
    trades["pnl"] = trades["net_edge"] * notional

    summary = {
        "total_bars_scanned": len(res_df),
        "signals_found": len(trades),
        "signal_rate_pct": round(100 * len(trades) / max(len(res_df), 1), 3),
        "avg_edge_per_trade": trades["pnl"].mean() if len(trades) else 0,
        "total_pnl": trades["pnl"].sum() if len(trades) else 0,
        "max_edge": trades["pnl"].max() if len(trades) else 0,
        "min_edge": trades["pnl"].min() if len(trades) else 0,
    }
    return res_df, trades, summary


# ----------------------------------------------------------------------
# 5. MAIN — end-to-end example (needs live Delta API access)
# ----------------------------------------------------------------------

if __name__ == "__main__":
    end = datetime.utcnow()
    start = end - timedelta(days=7)

    print("Fetching product list...")
    products = get_all_products()
    calls, puts, futures = filter_btc_chain(products)

    # pick nearest-expiry ATM strike as an example (you can loop over all strikes)
    strike = 65000  # example - replace with a real live strike from `calls`
    call_symbol = next(p["symbol"] for p in calls if p["strike_price"] == str(strike))
    put_symbol = next(p["symbol"] for p in puts if p["strike_price"] == str(strike))
    future_symbol = "BTCUSD"  # perpetual future symbol

    print("Fetching candles...")
    call_df = get_candles(call_symbol, "5m", start, end)
    put_df = get_candles(put_symbol, "5m", start, end)
    future_df = get_candles(future_symbol, "5m", start, end)

    print("Running backtest...")
    res_df, trades, summary = backtest_conversion_reversal(call_df, put_df, future_df, strike)

    print("\n=== BACKTEST SUMMARY ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    trades.to_csv("arbitrage_signals.csv", index=False)
    print("\nSaved detailed signals to arbitrage_signals.csv")
