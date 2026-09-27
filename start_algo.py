"""Algo start-up entry: read SuperTrend *now* and open the initial position.

Rule (what this implements, and only this):
    On start-up, look at the current SuperTrend direction.
      * BULLISH -> open LONG 1 future  + SELL 1 ATM CALL
      * BEARISH -> open SHORT 1 future + SELL 1 ATM PUT   (bearish mirror)

    On each 0DTE expiry (see src/expiry.py):
      * price beyond the future entry -> CLOSE the future + cancel SL (take profit)
      * otherwise                     -> ROLL: sell a new option struck at the
                                         future entry price (now OTM)

Run:
    python start_algo.py               # dry-run: compute & print the position only
    python start_algo.py --place       # ALSO send the start-up orders to Delta

Default is a dry-run: it fetches the live BTC index, computes the SuperTrend, and
prints the exact position to open — no orders are sent. Pass --place to actually
send the market orders through Delta. Orders go to whichever endpoint .env
selects: USE_TESTNET=true (the default) hits the testnet; set it false only when
you intend to trade real money. LONG/SHORT below is the *future* leg; the option
leg is always SOLD (short premium).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd

from config import CONFIG
from underlying import Underlying
from supertrend import TrendFilter

STRIKE_STEP = 200        # Delta BTC option strike spacing


def _atm_strike(spot: float, step: int = STRIKE_STEP) -> int:
    return int(round(spot / step) * step)


def startup_entry(cfg=CONFIG, place: bool = False):
    now = pd.Timestamp.now(tz="UTC")
    # enough history to warm up the higher-timeframe SuperTrend
    und = Underlying.load(start=now - pd.Timedelta(days=30), end=now, force=True)
    tf = TrendFilter.build(und.candles(), cfg)
    trend = tf.direction_at(now)
    spot = und.spot_at(now)

    if trend is None:
        print(f"[{now:%Y-%m-%d %H:%M UTC}] SuperTrend not warmed up yet — no position opened.")
        return None

    atm = _atm_strike(spot)
    stop = tf.stop_level_at(now)        # SuperTrend line = future stop-loss level
    if trend > 0:
        bias, fut_side, opt_type = "BULLISH", "LONG", "CALL"
    else:
        bias, fut_side, opt_type = "BEARISH", "SHORT", "PUT"

    print(f"[{now:%Y-%m-%d %H:%M UTC}] SuperTrend "
          f"({cfg.st_timeframe_hours}h, ATR{cfg.st_atr_period}, x{cfg.st_multiplier}) = {bias}")
    print(f"  BTC index (spot): {spot:,.1f}")
    print(f"  OPEN  -> {fut_side} 1 future @ {spot:,.1f}")
    print(f"  STOP  -> future SL @ SuperTrend line {stop:,.1f}")
    print(f"  SELL  -> 1 ATM {opt_type} @ strike {atm:,}")

    result = {"trend": trend, "spot": spot, "future_side": fut_side,
              "atm_strike": atm, "sell_option": opt_type, "stop_level": stop}

    if place:
        result["orders"] = _place_orders(spot, fut_side, opt_type, stop)

    return result


def _place_orders(spot: float, fut_side: str, opt_type: str, stop: float) -> dict:
    """Send the orders through Delta: open the future, attach a SuperTrend-line
    stop-loss, then SELL the ATM option. LONG->buy future / SHORT->sell future;
    the stop closes that leg (sell for a long, buy for a short)."""
    from delta_broker import DeltaBroker

    broker = DeltaBroker()
    where = "TESTNET" if broker.testnet else "*** LIVE (real money) ***"
    print(f"\n  Placing orders on {where}: {broker.base}")

    fut = broker.perpetual("BTCUSD")
    fut_order_side = "buy" if fut_side == "LONG" else "sell"
    fut_res = broker.place_market_order(fut["id"], 1, fut_order_side)
    print(f"  future: {fut_order_side.upper()} 1x {fut['symbol']} "
          f"-> order {fut_res.get('result', {}).get('id', '?')}")

    stop_side = "sell" if fut_side == "LONG" else "buy"
    sl_res = broker.place_stop_market_order(fut["id"], 1, stop_side, round(stop, 1))
    print(f"  stop:   {stop_side.upper()} 1x {fut['symbol']} @ {stop:,.1f} "
          f"-> order {sl_res.get('result', {}).get('id', '?')}")

    opt = broker.atm_option(spot, "C" if opt_type == "CALL" else "P")
    opt_res = broker.place_market_order(opt["id"], 1, "sell")
    print(f"  option: SELL 1x {opt['symbol']} "
          f"-> order {opt_res.get('result', {}).get('id', '?')}")

    return {"future": fut_res, "stop": sl_res, "option": opt_res}


if __name__ == "__main__":
    startup_entry(place="--place" in sys.argv[1:])
