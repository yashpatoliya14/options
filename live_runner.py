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
POSITION_CHECK_SEC = 30          # reconcile tracked position vs. the exchange this often


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
        self._pos_check_ts = 0.0                  # last exchange reconcile time

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

    # run-mode badge shown on every Telegram card
    _BADGE = {"PLACE·TESTNET": "🧪 Testnet", "PLACE·LIVE": "🔴 LIVE money",
              "DRY-RUN": "📝 Dry-run"}
    _RULE = "━━━━━━━━━━━━━━━━━━"

    def _notify(self, msg: str) -> None:
        """Log locally and push a plain Telegram line (tagged with the run mode)."""
        _log(msg)
        self.tg.send(f"[{self.tag}] {msg}")

    def _alert(self, emoji: str, title: str, rows: list[str], log_msg: str) -> None:
        """Log a plain one-liner and push a formatted HTML card to Telegram."""
        _log(log_msg)
        badge = self._BADGE.get(self.tag, self.tag)
        card = [f"{emoji} <b>{title}</b>", f"<i>{badge}</i>", self._RULE, *rows,
                self._RULE, f"<i>{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}</i>"]
        self.tg.send("\n".join(card))

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
            self._alert(
                "🟢" if trend > 0 else "🔴", f"ENTRY · {bias}",
                [f"{'📈 LONG' if trend>0 else '📉 SHORT'} <b>{self.qty}</b> {self.symbol} @ <code>{spot:,.1f}</code>",
                 f"🛑 Stop-loss @ <code>{stop:,.1f}</code>",
                 f"💸 Sold <code>{opt['symbol']}</code>",
                 f"⏱ Settles <b>{self.pos['opt_settle']:%H:%M UTC}</b>"],
                f"ENTRY {bias} — {'LONG' if trend>0 else 'SHORT'} {self.qty} {self.symbol} @ {spot:,.1f}, "
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
        self._alert(
            "🟢" if trend > 0 else "🔴", f"ENTRY · {bias}",
            [f"{'📈 LONG' if trend>0 else '📉 SHORT'} <b>{self.qty}</b> {self.symbol} filled @ <code>{entry:,.1f}</code>",
             f"🛑 Stop-loss @ <code>{stop:,.1f}</code>  <i>(id {sl_id})</i>",
             f"💸 Sold <code>{opt['symbol']}</code>",
             f"⏱ Settles <b>{self.pos['opt_settle']:%H:%M UTC}</b>"],
            f"ENTRY {bias} — {'LONG' if trend>0 else 'SHORT'} {self.qty} {self.symbol} filled @ {entry:,.1f}, "
            f"stop {stop:,.1f} (id {sl_id}), SOLD {opt['symbol']} "
            f"(settles {self.pos['opt_settle']:%H:%M UTC})")

    def _maybe_reconcile_position(self) -> None:
        """If the tracked position vanished from the exchange (manual close during
        testing, or the stop-loss firing), reset to flat so the next loop re-enters
        on the current SuperTrend signal. Only meaningful for placed trades with a
        real future id; dry-run / not-yet-placed positions are skipped.
        """
        if not self.place or self.pos is None:
            return
        fut_id = self.pos.get("fut_id")
        if not fut_id:
            return
        if time.time() - self._pos_check_ts < POSITION_CHECK_SEC:
            return
        self._pos_check_ts = time.time()
        try:
            size = self.broker.position_size(fut_id)
        except Exception as e:                    # keep running on a transient REST error
            _log(f"position reconcile failed: {e}")
            return
        if size != 0:
            return                                # still open on the exchange — nothing to do
        # future leg is gone (stop-loss fired or manual close). Cancel any resting stop,
        # BUY BACK the short option leg (the stop only closes the future — the option is
        # a separate contract and would otherwise sit naked until settlement), mark
        # closed, go flat. The main loop then re-enters immediately on the current signal.
        if self.pos.get("sl_id"):
            try:
                self.broker.cancel_order(fut_id, self.pos["sl_id"])
            except Exception:
                pass
        opt_symbol = self.pos.get("opt_symbol")
        opt_closed = False
        if opt_symbol:
            try:
                opt = self.broker.product_by_symbol(opt_symbol)
                if opt is not None:               # still live -> buy it back to close the short
                    self.broker.place_market_order(opt["id"], self.qty, "buy")
                    opt_closed = True
            except Exception as e:
                _log(f"option buy-back failed for {opt_symbol}: {e}")
        bias = self.pos["bias"]
        self.store.close_open(self.pos, "closed_externally")
        self.store.log("EXTERNAL_CLOSE", bias=bias, side=self.pos["side"],
                       symbol=self.symbol,
                       detail=f"future flat; option {'bought back' if opt_closed else 'already settled'}")
        self._alert(
            "⚪", "Position Closed Externally",
            [f"<b>{bias}</b> future gone (stop-loss fired / manual close)",
             "🧹 Cancelled resting stop",
             ("💸 Bought back the short option leg" if opt_closed
              else "ℹ️ Short option already settled — nothing to close"),
             "🔁 Re-entering now on the current SuperTrend signal"],
            f"EXTERNAL CLOSE {bias} — future flat (stop fired / manual close): cancelled "
            f"stop, option {'bought back' if opt_closed else 'already settled'}. "
            "Re-entering now on the current signal.")
        self.pos = None

    def handle_expiry(self) -> None:
        spot = self.current_spot()
        p = self.pos
        profit = (spot > p["entry"]) if p["bias"] == "BULLISH" else (spot < p["entry"])

        if profit:
            _log(f"EXPIRY {p['bias']}: spot {spot:,.1f} beyond entry {p['entry']:,.1f} "
                 "-> CUT ALL (take profit) + re-enter fresh on current signal")
            if self.place:
                close_side = "sell" if p["side"] > 0 else "buy"
                self.broker.place_market_order(p["fut_id"], self.qty, close_side)
                if p["sl_id"]:
                    self.broker.cancel_order(p["fut_id"], p["sl_id"])
                self.store.close_open(p, "take_profit")
                self.store.log("TAKE_PROFIT", bias=p["bias"], side=p["side"],
                               symbol=self.symbol, price=spot)
            gain = abs(spot - p["entry"])
            self._alert(
                "💰", f"TAKE-PROFIT · {p['bias']}",
                [f"Spot <code>{spot:,.1f}</code> beyond entry <code>{p['entry']:,.1f}</code>",
                 f"📊 Move in favour: <b>{gain:,.1f}</b>",
                 "✅ Cut all — closed future + cancelled stop",
                 "🔁 Re-entering now: SL on the SuperTrend line, ATM option sold at current price"],
                f"TAKE-PROFIT {p['bias']} — spot {spot:,.1f} beyond entry "
                f"{p['entry']:,.1f}: CUT ALL (closed future + cancelled stop), "
                "re-entering fresh on the current SuperTrend signal.")
            self.pos = None
            # User rule: on a profitable cut, don't wait for the next loop tick — open
            # a fresh position right away. open_position() takes the trade at the
            # CURRENT price, sets the stop on the NEAREST SuperTrend line (not the old
            # entry), and SELLS a new ATM option, following whatever the trend is now.
            self.open_position()
            return

        # ROLL: sell a new option struck at the future entry price
        opt_type = "C" if p["bias"] == "BULLISH" else "P"
        strike = self._round_strike(p["entry"])
        _log(f"EXPIRY {p['bias']}: spot {spot:,.1f} not beyond entry {p['entry']:,.1f} "
             f"-> ROLL: SELL {opt_type} @ {strike:,}")
        opt = self.broker.option_by_strike(strike, opt_type, self.asset)
        new_settle = self._settle_ts(opt)
        # SAFETY NET: never roll into a contract that is already settled. If we did,
        # opt_settle would stay <= now and the main loop would re-enter handle_expiry
        # every cycle — spamming Telegram (and, in --place, firing a SELL) every 5s.
        # broker.option_by_strike already filters to future expiries; this guards a
        # bad/edge API response too. Back off a few minutes instead of hammering.
        if new_settle <= _now():
            _log(f"ROLL skipped: nearest {opt_type} {opt['symbol']} settles "
                 f"{new_settle:%Y-%m-%d %H:%M UTC}, not in the future — backing off 5 min "
                 "(no order sent, no alert)")
            self.pos["opt_settle"] = _now() + pd.Timedelta(minutes=5)
            return
        if self.place:
            self.broker.place_market_order(opt["id"], self.qty, "sell")
        self.pos["opt_symbol"] = opt["symbol"]
        self.pos["opt_settle"] = new_settle
        self.pos["opt_strike"] = int(float(opt["strike_price"]))
        if self.place:
            self.store.update_option(self.pos)
            self.store.log("ROLL", bias=p["bias"], side=p["side"],
                           symbol=opt["symbol"], strike=self.pos["opt_strike"], price=spot)
        self._alert(
            "🔄", f"ROLL · {p['bias']}",
            [f"Spot <code>{spot:,.1f}</code> vs entry <code>{p['entry']:,.1f}</code> — not beyond yet",
             f"💸 Sold new <code>{opt['symbol']}</code>",
             f"⏱ Settles <b>{self.pos['opt_settle']:%H:%M UTC}</b>",
             "📌 Future kept open"],
            f"ROLL {p['bias']} — spot {spot:,.1f} vs entry {p['entry']:,.1f}: "
            f"SOLD {opt['symbol']} (settles {self.pos['opt_settle']:%H:%M UTC}), "
            "future kept open.")

    # --- main loop ---------------------------------------------------------
    def run(self) -> None:
        mode = "PLACE (TESTNET)" if (self.place and self.broker.testnet) else \
               "PLACE **LIVE**" if self.place else "DRY-RUN"
        self._alert(
            "🚀", "Live Runner Started",
            [f"⚙️ Mode: <b>{mode}</b>",
             f"🔗 Endpoint: <code>{self.broker.base}</code>",
             f"📐 SuperTrend {self.cfg.st_timeframe_hours}h · ATR{self.cfg.st_atr_period} · ×{self.cfg.st_multiplier}",
             f"📦 Size: <b>{self.qty}</b> {self.symbol}"],
            f"live runner starting — mode: {mode}, endpoint: {self.broker.base}")
        if self.pos is not None:
            self._alert(
                "🔄", "Resumed Open Position",
                [f"<b>{self.pos['bias']}</b> · entry <code>{self.pos['entry']:,.1f}</code>",
                 f"💸 Short <code>{self.pos['opt_symbol']}</code>",
                 f"⏱ Settles <b>{self.pos['opt_settle']:%H:%M UTC}</b>",
                 "🛡 Managing existing trade — <b>no new entry</b>"],
                f"RESUMED open {self.pos['bias']} position from state "
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
                    self._maybe_reconcile_position()   # detect manual/stop close first
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


