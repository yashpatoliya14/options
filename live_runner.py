"""Live runner for the SuperTrend 0DTE covered-directional strategy.

Streams the BTC index over Delta's public WebSocket, computes the SuperTrend from
REST candles, and manages the position through the same rules the backtest proved:

  ENTRY (when flat): SuperTrend bias ->
     BULLISH  -> LONG 1 future  + native SuperTrend-line STOP + SELL 1 ATM 0DTE CALL
     BEARISH  -> SHORT 1 future + native SuperTrend-line STOP + SELL 1 ATM 0DTE PUT
  The stop is a real stop-loss order resting on Delta, so the exchange enforces the
  trend-flip exit — the runner does not have to babysit price intraday.

  0DTE EXPIRY (when the sold option settles):
     BULLISH  spot > entry -> CLOSE future + cancel stop (take profit), go flat
              else         -> ROLL: sell a new CALL struck at the future entry price
     BEARISH  spot < entry -> CLOSE + cancel stop, go flat
              else         -> ROLL: sell a new PUT at the future entry price

Run:
    python live_runner.py                # DRY-RUN: stream + decide + log, no orders
    python live_runner.py --place        # place orders (TESTNET unless USE_TESTNET=false)

Safety: dry-run is the default. --place sends real orders to whatever endpoint .env
selects; USE_TESTNET=true (default) keeps it on the testnet. In-memory position
state only — restarting starts flat, so flatten manually on Delta before a restart
if a position is open. Ctrl-C exits cleanly.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd
import websocket  # websocket-client

from config import CONFIG
from underlying import Underlying
from supertrend import TrendFilter
from delta_broker import DeltaBroker
from notify import Telegram
from store import Store

STRIKE_STEP = 200
WARMUP_DAYS = 40                 # history pulled to warm the 8h SuperTrend
TREND_REFRESH_SEC = 900          # re-pull candles / recompute SuperTrend every 15 min


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}] {msg}", flush=True)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() == "true"


class LiveRunner:
    def __init__(self, cfg=CONFIG, place: bool | None = None):
        self.cfg = cfg
        # placement is controlled from .env (PLACE_ORDERS); an explicit arg overrides
        self.place = _env_bool("PLACE_ORDERS", False) if place is None else place
        self.broker = DeltaBroker()
        self.tg = Telegram()
        self.qty = int(os.getenv("TRADE_QTY", "1"))
        self.symbol = os.getenv("UNDERLYING_SYMBOL", "BTCUSD").strip()
        self.asset = os.getenv("UNDERLYING_ASSET", "BTC").strip()
        self.tag = ("PLACE·TESTNET" if (self.place and self.broker.testnet)
                    else "PLACE·LIVE" if self.place else "DRY-RUN")
        self.ws_url = ("wss://socket-ind.testnet.deltaex.org" if self.broker.testnet
                       else "wss://socket.india.delta.exchange")
        self.spot: float | None = None          # live BTC index from the websocket
        self._trend_filter: TrendFilter | None = None
        self._und: Underlying | None = None
        self._trend_ts = 0.0
        self._stop = threading.Event()
        # durable position state: reload any still-open position so an accidental
        # restart manages the live trade instead of opening a second one.
        # DB path is a fixed constant (STATE_DB in config.py), not an env var.
        self.store = Store()
        self.pos: dict | None = self.store.load_open()

    # --- market data: SuperTrend (REST) ------------------------------------
    def refresh_trend(self) -> None:
        now = _now()
        self._und = Underlying.load(start=now - pd.Timedelta(days=WARMUP_DAYS),
                                    end=now, force=True)
        self._trend_filter = TrendFilter.build(self._und.candles(), self.cfg)
        self._trend_ts = time.time()

    def _maybe_refresh_trend(self) -> None:
        if time.time() - self._trend_ts >= TREND_REFRESH_SEC:
            try:
                self.refresh_trend()
            except Exception as e:               # keep running on a transient REST error
                _log(f"trend refresh failed: {e}")

    def current_spot(self) -> float:
        if self.spot is not None:
            return self.spot
        if self._und is not None:                # websocket not warmed yet
            return self._und.spot_at(_now())
        raise RuntimeError("no price available yet")

    def _notify(self, msg: str) -> None:
        """Log locally and push a Telegram alert (tagged with the run mode)."""
        _log(msg)
        self.tg.send(f"[{self.tag}] {msg}")

    # --- market data: live index (WebSocket) -------------------------------
    def _on_open(self, ws) -> None:
        ws.send(json.dumps({
            "type": "subscribe",
            "payload": {"channels": [{"name": "v2/ticker", "symbols": ["BTCUSD"]}]},
        }))
        _log(f"websocket subscribed to v2/ticker BTCUSD on {self.ws_url}")

    def _on_message(self, ws, message: str) -> None:
        try:
            data = json.loads(message)
        except ValueError:
            return
        # v2/ticker carries the underlying index in `spot_price`
        px = data.get("spot_price") or data.get("mark_price")
        if px:
            self.spot = float(px)

    def _ws_thread(self) -> None:
        while not self._stop.is_set():
            try:
                ws = websocket.WebSocketApp(
                    self.ws_url, on_open=self._on_open, on_message=self._on_message)
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                _log(f"websocket error: {e}")
            if not self._stop.is_set():
                time.sleep(3)                    # reconnect backoff

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _fill_price(order_res: dict, fallback: float) -> float:
        r = order_res.get("result", {}) if isinstance(order_res, dict) else {}
        for k in ("average_fill_price", "avg_fill_price", "price"):
            v = r.get(k)
            if v:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return fallback

    @staticmethod
    def _settle_ts(product: dict) -> pd.Timestamp:
        return pd.Timestamp(product["settlement_time"]).tz_convert("UTC")

    @staticmethod
    def _round_strike(px: float) -> int:
        return int(round(px / STRIKE_STEP) * STRIKE_STEP)

    # --- strategy actions --------------------------------------------------
    def open_position(self) -> None:
        # never open a second trade on top of a placed one that survived a restart
        if self.place and self.store.load_open() is not None:
            self.pos = self.store.load_open()
            return
        now = _now()
        trend = self._trend_filter.direction_at(now)
        if trend is None:
            return                                # SuperTrend not warmed yet
        spot = self.current_spot()
        line = self._trend_filter.stop_level_at(now)
        stop = line if line is not None else spot * (0.98 if trend > 0 else 1.02)
        bias = "BULLISH" if trend > 0 else "BEARISH"
        opt_type = "C" if trend > 0 else "P"
        fut_order_side = "buy" if trend > 0 else "sell"
        stop_side = "sell" if trend > 0 else "buy"

        _log(f"ENTRY {bias}: {'LONG' if trend>0 else 'SHORT'} future @ {spot:,.1f}, "
             f"SL @ {stop:,.1f}, SELL ATM {opt_type}")

        if not self.place:
            opt = self.broker.atm_option(spot, opt_type, self.asset)   # public read: real contract
            self.pos = {"bias": bias, "side": trend, "entry": spot, "sl_id": None,
                        "fut_id": None, "opt_symbol": opt["symbol"],
                        "opt_settle": self._settle_ts(opt),
                        "opt_strike": int(float(opt["strike_price"]))}
            self._notify(f"ENTRY {bias} — {'LONG' if trend>0 else 'SHORT'} {self.qty} {self.symbol} @ {spot:,.1f}, "
                         f"SL {stop:,.1f}, SELL {opt['symbol']} (settles "
                         f"{self.pos['opt_settle']:%H:%M UTC}) [dry-run]")
            return

        fut = self.broker.perpetual(self.symbol)
        fr = self.broker.place_market_order(fut["id"], self.qty, fut_order_side)
        entry = self._fill_price(fr, spot)
        sl = self.broker.place_stop_market_order(fut["id"], self.qty, stop_side, round(stop, 1))
        sl_id = sl.get("result", {}).get("id")
        opt = self.broker.atm_option(spot, opt_type, self.asset)
        self.broker.place_market_order(opt["id"], self.qty, "sell")
        self.pos = {"bias": bias, "side": trend, "entry": entry, "sl_id": sl_id,
                    "fut_id": fut["id"], "opt_symbol": opt["symbol"],
                    "opt_settle": self._settle_ts(opt),
                    "opt_strike": int(float(opt["strike_price"]))}
        self.store.save_open(self.pos)          # durable: survives a restart
        self.store.log("ENTRY", bias=bias, side=trend, symbol=self.symbol,
                       strike=self.pos["opt_strike"], price=entry,
                       detail=f"SL {stop:.1f} (id {sl_id}), SOLD {opt['symbol']}")
        self._notify(f"ENTRY {bias} — {'LONG' if trend>0 else 'SHORT'} {self.qty} {self.symbol} filled @ {entry:,.1f}, "
                     f"stop {stop:,.1f} (id {sl_id}), SOLD {opt['symbol']} "
                     f"(settles {self.pos['opt_settle']:%H:%M UTC})")

    def handle_expiry(self) -> None:
        spot = self.current_spot()
        p = self.pos
        profit = (spot > p["entry"]) if p["bias"] == "BULLISH" else (spot < p["entry"])

        if profit:
            _log(f"EXPIRY {p['bias']}: spot {spot:,.1f} beyond entry {p['entry']:,.1f} "
                 "-> CLOSE future + cancel stop (take profit)")
            if self.place:
                close_side = "sell" if p["side"] > 0 else "buy"
                self.broker.place_market_order(p["fut_id"], self.qty, close_side)
                if p["sl_id"]:
                    self.broker.cancel_order(p["fut_id"], p["sl_id"])
                self.store.close_open(p, "take_profit")
                self.store.log("TAKE_PROFIT", bias=p["bias"], side=p["side"],
                               symbol=self.symbol, price=spot)
            self._notify(f"TAKE-PROFIT {p['bias']} — spot {spot:,.1f} beyond entry "
                         f"{p['entry']:,.1f}: CLOSED future + cancelled stop. Now flat.")
            self.pos = None                       # go flat; next loop re-enters on trend
            return

        # ROLL: sell a new option struck at the future entry price
        opt_type = "C" if p["bias"] == "BULLISH" else "P"
        strike = self._round_strike(p["entry"])
        _log(f"EXPIRY {p['bias']}: spot {spot:,.1f} not beyond entry {p['entry']:,.1f} "
             f"-> ROLL: SELL {opt_type} @ {strike:,}")
        opt = self.broker.option_by_strike(strike, opt_type, self.asset)
        if self.place:
            self.broker.place_market_order(opt["id"], self.qty, "sell")
        self.pos["opt_symbol"] = opt["symbol"]
        self.pos["opt_settle"] = self._settle_ts(opt)
        self.pos["opt_strike"] = int(float(opt["strike_price"]))
        if self.place:
            self.store.update_option(self.pos)
            self.store.log("ROLL", bias=p["bias"], side=p["side"],
                           symbol=opt["symbol"], strike=self.pos["opt_strike"], price=spot)
        self._notify(f"ROLL {p['bias']} — spot {spot:,.1f} vs entry {p['entry']:,.1f}: "
                     f"SOLD {opt['symbol']} (settles {self.pos['opt_settle']:%H:%M UTC}), "
                     "future kept open.")

    # --- main loop ---------------------------------------------------------
    def run(self) -> None:
        mode = "PLACE (TESTNET)" if (self.place and self.broker.testnet) else \
               "PLACE **LIVE**" if self.place else "DRY-RUN"
        self._notify(f"live runner starting — mode: {mode}, endpoint: {self.broker.base}")
        if self.pos is not None:
            self._notify(f"RESUMED open {self.pos['bias']} position from state "
                         f"(entry {self.pos['entry']:,.1f}, short {self.pos['opt_symbol']}, "
                         f"settles {self.pos['opt_settle']:%H:%M UTC}) — managing it, NOT opening a new trade.")
        self.refresh_trend()
        threading.Thread(target=self._ws_thread, daemon=True).start()

        # wait briefly for the first websocket tick (fall back to REST spot)
        for _ in range(20):
            if self.spot is not None:
                break
            time.sleep(0.5)

        try:
            while not self._stop.is_set():
                self._maybe_refresh_trend()
                try:
                    if self.pos is None:
                        self.open_position()
                    elif _now() >= self.pos["opt_settle"]:
                        self.handle_expiry()
                except Exception as e:
                    _log(f"cycle error: {e}")
                time.sleep(5)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            _log("live runner stopped.")


if __name__ == "__main__":
    # placement is env-driven (PLACE_ORDERS in .env); --place forces it on regardless
    force = "--place" in sys.argv[1:]
    LiveRunner(place=True if force else None).run()


