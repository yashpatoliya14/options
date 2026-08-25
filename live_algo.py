"""
live_algo.py -- runs the SAME engine.py used by backtest.py against live
market data. TRADING_MODE in .env controls behavior:

    TRADING_MODE=paper  -> live market data, simulated fills, no real orders,
                            still sends Telegram notifications (recommended
                            first step after run_diagnostic.py)
    TRADING_MODE=live   -> real orders placed on Delta Exchange

Restart safety (spec section 14): on startup, this reconciles SQLite's last
known open trade against Delta's actual open positions before deciding
whether to treat the engine as having an existing position. It will NEVER
open a new position if the exchange already shows one open for this strategy.

Run loop: polls for new closed 3H candles, feeds each one to engine.py in
order, persists every event to SQLite, and sends Telegram notifications.
"""
import argparse
import logging
import time
from typing import List, Optional

from config import CONFIG
from broker import Position
from engine import StrategyEngine, EngineEvent
from strategy.supertrend import Candle
from exchange.delta_client import DeltaClient
from storage.state_store import StateStore
from notifications.telegram import TelegramNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("live_algo")


class PaperBroker:
    """
    Wraps DeltaClient for REAL market data (option chains, candles) but never
    places real orders -- simulates fills using the live quoted premium plus
    configured slippage. Used when TRADING_MODE=paper.
    """
    def __init__(self, cfg, real_client: DeltaClient):
        self.cfg = cfg
        self.real = real_client
        self._positions = {}

    def get_available_expiries(self, underlying, as_of_timestamp=None):
        return self.real.get_available_expiries(underlying, as_of_timestamp)

    def get_option_chain(self, underlying, expiry, timestamp=None):
        return self.real.get_option_chain(underlying, expiry, timestamp)

    def estimate_margin_per_lot(self, quote):
        return self.real.estimate_margin_per_lot(quote)

    def place_sell_order(self, quote, quantity):
        from broker import OrderResult
        slip = quote.premium * (self.cfg.slippage_pct / 100.0)
        fill = max(0.0, quote.premium - slip)
        self._positions[quote.symbol] = (quote, quantity, fill)
        return OrderResult(True, f"paper-{quote.symbol}-{int(time.time())}", fill, quantity, "paper_fill")

    def close_position(self, position: Position):
        from broker import OrderResult
        chain = self.real.get_option_chain(CONFIG.underlying_asset, position.expiry)
        match = next((q for q in chain if q.strike == position.strike and q.option_type == position.option_type), None)
        if match is None:
            return OrderResult(False, None, None, position.quantity, "no_live_quote_for_close")
        slip = match.premium * (self.cfg.slippage_pct / 100.0)
        fill = match.premium + slip
        self._positions.pop(position.symbol, None)
        return OrderResult(True, f"paper-close-{position.symbol}", fill, position.quantity, "paper_close")

    def get_open_positions(self):
        return []  # paper mode has no real exchange state to reconcile against


def fetch_timeframe_candles(client: DeltaClient, symbol: str, start_ts: int, end_ts: int) -> List[Candle]:
    res = CONFIG.timeframe
    duration = 7200 if res == "2h" else 3600
    raw = client.get_historical_candles(symbol, res, start_ts, end_ts)
    if not raw:
        return []
    candles = []
    for r in sorted(raw, key=lambda x: int(x["time"])):
        candles.append(Candle(
            timestamp=int(r["time"]) + duration,
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"])
        ))
    return candles


def build_candle_stream_poller(client: DeltaClient, last_seen_ts: Optional[int]):
    """
    Polls Delta for the most recently CLOSED candle and yields it once,
    when it's new. Never yields a still-forming candle.
    """
    def poll() -> Optional[Candle]:
        now = int(time.time())
        duration = 7200 if CONFIG.timeframe == "2h" else 3600
        # Fetch candles for the last 3 periods
        candles = fetch_timeframe_candles(client, CONFIG.underlying_symbol, now - 3 * duration, now)
        
        if not candles:
            return None
            
        # Get the most recent candle
        latest = candles[-1]
        ts = latest.timestamp
        
        # Prevent look-ahead bias in live trading: 
        # Only process the candle if its precise close time has passed.
        if now < ts:
            if len(candles) < 2:
                return None
            latest = candles[-2]
            ts = latest.timestamp
        
        nonlocal_last = poll.last_seen
        if nonlocal_last is not None and ts <= nonlocal_last:
            return None
            
        poll.last_seen = ts
        return latest
        
    poll.last_seen = last_seen_ts
    return poll


def reconcile_startup_state(store: StateStore, cfg, real_client: DeltaClient) -> Optional[Position]:
    """
    Spec section 14: on startup, read the ACTUAL exchange position and never
    open a duplicate. Cross-checks against SQLite's record of what we think
    is open.
    """
    exchange_positions = real_client.get_open_positions()
    local_open = store.get_open_trade()

    if not exchange_positions and not local_open:
        log.info("Startup reconciliation: no open position on exchange or locally. Clean start.")
        return None

    if exchange_positions and not local_open:
        log.warning(
            "Startup reconciliation: exchange shows an open position but SQLite has none. "
            "Adopting exchange state as source of truth to avoid a duplicate/orphaned position: %s",
            exchange_positions[0],
        )
        p = exchange_positions[0]
        direction = "BUY" if p.option_type == "put" else "SELL"
        return Position(
            symbol=p.symbol, option_type=p.option_type, strike=p.strike, expiry=p.expiry,
            side="sell", quantity=p.quantity, entry_premium=p.entry_premium,
            entry_timestamp=int(time.time()), strategy_direction=direction,
        )

    if local_open and not exchange_positions:
        log.warning(
            "Startup reconciliation: SQLite shows an open trade but exchange shows none "
            "(likely closed manually or outside the bot). Marking local trade as closed "
            "with reason 'reconciled_missing_on_exchange'."
        )
        store.record_trade_exit(int(time.time()), None, "reconciled_missing_on_exchange")
        return None

    # both show open -- trust exchange for the live fields, keep local for direction context
    p = exchange_positions[0]
    direction = local_open.get("signal", "BUY" if p.option_type == "put" else "SELL")
    log.info("Startup reconciliation: exchange and local state agree an open position exists.")
    return Position(
        symbol=p.symbol, option_type=p.option_type, strike=p.strike or local_open.get("strike"),
        expiry=p.expiry or local_open.get("expiry"), side="sell", quantity=p.quantity,
        entry_premium=p.entry_premium or local_open.get("entry_premium"),
        entry_timestamp=local_open.get("entry_timestamp", int(time.time())),
        strategy_direction=direction,
    )


def warm_up_engine(client: DeltaClient, engine: StrategyEngine):
    """
    Fetch enough historical candles to fully warm up the Supertrend indicator
    (e.g., 200 candles) so the bot is ready to evaluate signals
    immediately instead of waiting 48+ hours for the indicator to initialize.
    """
    now = int(time.time())
    duration = 7200 if CONFIG.timeframe == "2h" else 3600
    start_ts = now - (200 * duration)
    
    try:
        candles = fetch_timeframe_candles(client, CONFIG.underlying_symbol, start_ts, now)
        if not candles:
            return
            
        # We only want to feed CLOSED candles to warm up the supertrend
        if now < candles[-1].timestamp:
            candles = candles[:-1]
            
        log.info(f"Warming up Supertrend with {len(candles)} historical {CONFIG.timeframe} candles...")
        for candle in candles:
            engine.supertrend.update(candle)
    except Exception as e:
        log.error(f"Failed to warm up engine: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-seconds", type=int, default=60,
                         help="how often to check for a newly closed candle")
    parser.add_argument("--demo-trade", action="store_true", 
                         help="Place and immediately close a 1-lot order to confirm exchange connectivity")
    args = parser.parse_args()

    mode = CONFIG.trading_mode
    log.info(f"Starting live_algo.py in TRADING_MODE={mode}")

    store = StateStore(CONFIG.sqlite_path)
    notifier = TelegramNotifier(CONFIG)
    real_client = DeltaClient(CONFIG)

    if args.demo_trade:
        if mode != "live":
            log.error("Demo trade requires TRADING_MODE=live")
            return
        log.info("--- DEMO TRADE MODE ---")
        expiries = real_client.get_available_expiries(CONFIG.underlying_asset)
        if not expiries:
            log.error("Demo trade failed: No expiries found.")
            return
        chain = real_client.get_option_chain(CONFIG.underlying_asset, expiries[0])
        if not chain:
            log.error("Demo trade failed: No options found.")
            return
        # Pick the first put
        quote = next((q for q in chain if q.option_type == "put"), None)
        if not quote:
            log.error("Demo trade failed: No puts found.")
            return
        
        log.info(f"Placing demo SELL order for 1 lot of {quote.symbol}...")
        order = real_client.place_sell_order(quote, 1)
        if not order.success:
            log.error(f"Demo trade sell failed: {order.message}")
            return
        log.info(f"Demo SELL success. Order ID: {order.order_id}. Now closing...")
        pos = Position(symbol=quote.symbol, option_type=quote.option_type, strike=quote.strike, expiry=quote.expiry, side="sell", quantity=1, entry_premium=order.filled_premium or quote.premium, entry_timestamp=int(time.time()), strategy_direction="")
        close_order = real_client.close_position(pos)
        if not close_order.success:
            log.error(f"Demo trade close failed: {close_order.message}. WARNING: You may have an open 1-lot position!")
            return
        log.info(f"Demo CLOSE success. Order ID: {close_order.order_id}. Exchange connectivity confirmed!")
        return

    existing_position = None
    broker = None

    if mode == "live":
        if not CONFIG.delta_api_key or not CONFIG.delta_api_secret:
            log.error("TRADING_MODE=live requires DELTA_API_KEY/DELTA_API_SECRET. Aborting.")
            return
        existing_position = reconcile_startup_state(store, CONFIG, real_client)
        broker = real_client
    elif mode == "paper":
        broker = PaperBroker(CONFIG, real_client)
        local_open = store.get_open_trade()
        if local_open:
            existing_position = Position(
                symbol=f"{local_open['option_type'][0].upper()}-{CONFIG.underlying_asset}-{int(local_open['strike'])}-{local_open['expiry']}",
                option_type=local_open["option_type"], strike=local_open["strike"], expiry=local_open["expiry"],
                side="sell", quantity=local_open["quantity"], entry_premium=local_open["entry_premium"],
                entry_timestamp=local_open["entry_timestamp"], strategy_direction=local_open["signal"],
            )
            log.info("Paper mode: resuming previously open paper position from SQLite.")
    else:
        log.error(f"Unknown TRADING_MODE '{mode}'. Use 'paper' or 'live'. (Use backtest.py for historical replay.)")
        return

    def on_event(e: EngineEvent) -> None:
        try:
            if e.kind == "signal":
                store.record_signal(e.timestamp, e.payload["trend"], e.payload["reference_price"], e.payload["signal_change"])
                if e.payload["signal_change"]:
                    notifier.signal(e.payload["trend"], e.payload["reference_price"], e.payload["reference_price"], CONFIG)
            elif e.kind == "trade_entry":
                store.record_trade_entry(
                    e.timestamp, e.payload["signal"], e.payload["option_type"], e.payload["strike"],
                    e.payload["expiry"], e.payload["quantity"], e.payload["premium"],
                )
                store.record_order(
                    e.timestamp, e.payload.get("order_id"), "sell", e.payload.get("symbol", ""),
                    e.payload["quantity"], e.payload["premium"], True, "entry"
                )
                notifier.trade_entry(
                    e.payload["signal"], e.payload["option_type"], e.payload["strike"], e.payload["expiry"],
                    e.payload["premium"], e.payload["underlying_price"],
                )
                log.info(f"TRADE ENTRY: {e.payload}")
            elif e.kind == "trade_exit":
                entry_premium = e.payload["entry_premium"]
                exit_premium = e.payload.get("exit_premium") or 0.0
                qty = e.payload["quantity"]
                gross = (entry_premium - exit_premium) * qty
                fees = (entry_premium + exit_premium) * qty * (CONFIG.fee_pct / 100.0)
                net = gross - fees
                store.record_trade_exit(e.timestamp, exit_premium, e.payload["reason"])
                store.record_order(
                    e.timestamp, e.payload.get("order_id"), "buy", e.payload.get("symbol", ""),
                    qty, exit_premium, True, e.payload["reason"]
                )
                notifier.trade_exit(e.payload["reason"], entry_premium, exit_premium, gross, fees, net)
                log.info(f"TRADE EXIT: {e.payload} net_pnl={net:.2f}")
            elif e.kind == "trade_skipped":
                log.info(f"Trade skipped: {e.payload}")
            elif e.kind == "error":
                store.record_error(e.timestamp, e.payload.get("context", ""), str(e.payload.get("reason", "")))
                notifier.error(e.payload.get("context", ""), str(e.payload.get("reason", "")))
                log.error(f"ENGINE ERROR: {e.payload}")
        except Exception:
            log.exception("Failed while handling engine event %s", e.kind)

    engine = StrategyEngine(CONFIG, broker, on_event, existing_position=existing_position)
    warm_up_engine(real_client, engine)

    last_ts = store.get_state("last_candle_timestamp")
    poller = build_candle_stream_poller(real_client, last_ts)

    startup_msg = f"Live algo started in {mode.upper()} mode.\nAsset: {CONFIG.underlying_symbol}"
    log.info(startup_msg)
    notifier.status(startup_msg)
    log.info(f"Entering poll loop. Waiting for closed {CONFIG.timeframe} candles...")
    
    last_status_sent = time.time()
    last_6h_status_sent = time.time() - (6 * 3600) + 60
    last_processed_update_id = 0
    force_open = False
    force_close = False
    
    while True:
        # Telegram updates
        updates = notifier.get_updates()
        for update in updates:
            update_id = update.get("update_id", 0)
            if update_id > last_processed_update_id:
                last_processed_update_id = update_id
                msg_text = update.get("message", {}).get("text", "").lower().strip()
                callback_query = update.get("callback_query")
                
                parts = []
                if callback_query:
                    cb_data = callback_query.get("data", "").lower()
                    cb_id = callback_query.get("id")
                    notifier.answer_callback(cb_id)
                    if cb_data == "close_o1":
                        parts = ["close", "o1"]
                    elif cb_data == "open_o1":
                        parts = ["open", "o1"]
                    elif cb_data == "clear_o1":
                        parts = ["clear", "o1"]
                    elif cb_data == "sl_o1":
                        notifier.status("To update SL for O1, please send the command:\n`/sl O1 <price>`", parse_mode="Markdown")
                elif msg_text:
                    if msg_text in ["/logs", "logs"]:
                        last_6h_status_sent = 0
                    parts = msg_text.split()
                    
                if len(parts) >= 3 and parts[0] == "/sl" and parts[1] == "o1":
                    try:
                        new_sl = float(parts[2])
                        if engine.current_position:
                            engine._manual_sl_threshold = new_sl
                            notifier.status(f"✅ SL for O1 manually updated to ${new_sl:,.2f}")
                        else:
                            notifier.status("❌ No active position for O1")
                    except Exception:
                        notifier.status("❌ Invalid format. Use: `/sl O1 25.5`", parse_mode="Markdown")
                        
                elif len(parts) >= 2 and parts[0] == "close" and parts[1] == "o1":
                    force_close = True
                    notifier.status("⏳ Command received: Queued CLOSE for O1 (will execute on next poll).")
                elif len(parts) >= 2 and parts[0] == "open" and parts[1] == "o1":
                    force_open = True
                    notifier.status("⏳ Command received: Queued OPEN for O1 (will execute on next poll).")
                elif len(parts) >= 2 and parts[0] == "clear" and parts[1] == "o1":
                    if engine.current_position:
                        engine.current_position = None
                        store.record_trade_exit(int(time.time()), 0.0, "manual_state_clear")
                        notifier.status("✅ Local state cleared. Bot now thinks position is closed.")
                    else:
                        notifier.status("❌ Already clear.")

        # Send 12-hour status update
        if time.time() - last_status_sent >= 12 * 3600:
            try:
                notifier.status(startup_msg)
            except Exception:
                pass
            last_status_sent = time.time()

        if time.time() - last_6h_status_sent >= 6 * 3600:
            pos = engine.current_position
            lines = ["🌟 *OPTIONS ALGO STATUS* 🌟\n_Open Positions:_"]
            keyboard = []
            
            if pos:
                current_sl = engine._manual_sl_threshold if engine._manual_sl_threshold is not None else pos.entry_premium * (1 + CONFIG.stop_loss_percent / 100.0)
                lines.append(f"🔹 `[O1]` *{pos.symbol}* {pos.side.upper()} {pos.option_type.upper()} {pos.strike} | Size: {pos.quantity} | Entry: ${pos.entry_premium:.2f} | SL: ${current_sl:.2f}")
                keyboard.append([
                    {"text": "Close O1", "callback_data": "close_o1"},
                    {"text": "Update SL O1", "callback_data": "sl_o1"}
                ])
                keyboard.append([
                    {"text": "Clear State (If closed)", "callback_data": "clear_o1"},
                    {"text": "Force Reopen", "callback_data": "open_o1"}
                ])
            else:
                lines.append(f"🔸 `[O1]` None")
                keyboard.append([
                    {"text": "Place Again O1", "callback_data": "open_o1"}
                ])
                
            reply_markup = {"inline_keyboard": keyboard}
            try:
                notifier.status("\n".join(lines), parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                pass
            last_6h_status_sent = time.time()

        try:
            candle = poller()
            if candle is not None:
                log.info(f"New closed candle: t={candle.timestamp} close={candle.close}")
                
                if force_close:
                    if engine.current_position:
                        engine._close_current_position(candle.timestamp, reason="manual_close")
                        notifier.status("✅ Manual Close Executed for O1")
                    else:
                        notifier.status("❌ Failed to Close: No active position for O1")
                    force_close = False
                    
                if force_open:
                    from strategy.supertrend import SupertrendPoint, Trend
                    trend = engine._last_trend if engine._last_trend else Trend.BULLISH
                    point = SupertrendPoint(
                        timestamp=candle.timestamp,
                        trend=trend,
                        value=candle.close * 0.9,
                        reference_price=candle.close,
                        signal_change=True
                    )
                    engine._attempt_open(point)
                    force_open = False
                    
                if hasattr(broker, "estimate_margin_per_lot"):
                    pass  # broker already carries CONFIG-scoped clock-free lookups for live mode
                engine.on_candle_close(candle)
                store.set_state("last_candle_timestamp", candle.timestamp)
        except Exception:
            log.exception("Error in main poll loop")
            store.record_error(int(time.time()), "poll_loop", "unhandled exception, see logs")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
