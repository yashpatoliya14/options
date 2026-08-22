"""
BTC Options Arbitrage — Live Trading Algorithm
================================================
Put-Call Parity arbitrage on Delta Exchange.

Modes:
  --mode dry_run  : Log signals only, no real orders (DEFAULT)
  --mode live     : Place real orders on Delta Exchange

Usage:
  python live_algo.py                  # dry run
  python live_algo.py --mode dry_run   # dry run (explicit)
  python live_algo.py --mode live      # REAL trading

Requirements:
  pip install requests websocket-client pandas numpy
"""

import sys
import os
import json
import time
import hmac
import hashlib
import argparse
import csv
from datetime import datetime, timezone, timedelta
from collections import deque

# Force UTF-8 on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import requests
import numpy as np
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

# Delta Exchange API credentials
API_KEY    = os.getenv("DELTA_API_KEY", "")
API_SECRET = os.getenv("DELTA_API_SECRET", "")

# Telegram credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Trading parameters
UNDERLYING     = "BTC"
STRIKE         = 65000          # Target strike (will pick nearest available)
CONTRACT_SIZE  = 0.001          # BTC per contract (Delta minimum)
LEVERAGE       = int(os.getenv("LEVERAGE", "10"))
INITIAL_MARGIN = float(os.getenv("INITIAL_MARGIN", "1000"))

# Risk limits
MAX_OPEN_POSITIONS    = 5       # Max simultaneous open positions
MAX_TRADES_PER_DAY    = 50      # Stop trading after this many trades/day
COOLDOWN_SECONDS      = 300     # 5 min between trades
MIN_NET_EDGE_USD      = 0.01    # Minimum net edge to trigger trade ($)
STOP_LOSS_PORTFOLIO   = 500     # Stop trading if portfolio drops below Rs.500

# Fee model (Delta Exchange)
TAKER_FEE_OPTION_BPS  = 3.0
TAKER_FEE_FUTURE_BPS  = 5.0
SLIPPAGE_USD_OPTION   = 3.0
SLIPPAGE_USD_FUTURE   = 1.0

# Scan interval
SCAN_INTERVAL_SECS    = 30      # Check for signals every 30 seconds

# Exchange
BASE_URL = "https://api.delta.exchange/v2"
USD_TO_INR = 83.50

# ===================================================================
#  OUTPUT FILES
# ===================================================================
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE   = os.path.join(OUTPUT_DIR, "algo_log.csv")
POS_FILE   = os.path.join(OUTPUT_DIR, "positions.json")

# ===================================================================
#  TELEGRAM NOTIFIER
# ===================================================================
def send_telegram_message(text):
    """Send message to Telegram if credentials are set."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"    [!] Telegram error: {e}")


# ===================================================================
#  DELTA EXCHANGE API CLIENT
# ===================================================================
class DeltaExchangeClient:
    """Handles all Delta Exchange API interactions."""

    def __init__(self, api_key="", api_secret="", base_url=BASE_URL):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _sign(self, method, path, payload=""):
        """Generate HMAC signature for authenticated endpoints."""
        timestamp = str(int(time.time()))
        signature_data = method + timestamp + path + payload
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_data.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": self.api_key,
            "timestamp": timestamp,
            "signature": signature,
        }

    def _get(self, path, params=None, auth=False):
        """GET request."""
        url = f"{self.base_url}{path}"
        headers = {}
        if auth and self.api_key:
            headers = self._sign("GET", path)
        resp = self.session.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path, data=None, auth=True):
        """POST request (authenticated)."""
        url = f"{self.base_url}{path}"
        payload = json.dumps(data) if data else ""
        headers = self._sign("POST", path, payload) if auth else {}
        resp = self.session.post(url, data=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path, auth=True):
        """DELETE request (authenticated)."""
        url = f"{self.base_url}{path}"
        headers = self._sign("DELETE", path) if auth else {}
        resp = self.session.delete(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    # --- Public endpoints ---

    def get_products(self, contract_types=None):
        """Fetch all products."""
        params = {}
        if contract_types:
            params["contract_types"] = contract_types
        return self._get("/products", params=params).get("result", [])

    def get_ticker(self, symbol):
        """Get latest ticker for a symbol."""
        result = self._get(f"/tickers/{symbol}")
        return result.get("result", {})

    def get_orderbook(self, symbol, depth=5):
        """Get L2 orderbook."""
        result = self._get(f"/l2orderbook/{symbol}")
        return result.get("result", {})

    # --- Authenticated endpoints ---

    def get_wallet(self):
        """Get wallet balances."""
        return self._get("/wallet/balances", auth=True).get("result", [])

    def get_positions(self):
        """Get open positions."""
        return self._get("/positions", auth=True).get("result", [])

    def place_order(self, symbol, size, side, order_type="market", price=None,
                    time_in_force="ioc", reduce_only=False):
        """Place an order."""
        order = {
            "product_id": self._get_product_id(symbol),
            "size": size,
            "side": side,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "reduce_only": reduce_only,
        }
        if price and order_type == "limit":
            order["limit_price"] = str(price)
        return self._post("/orders", data=order)

    def cancel_all_orders(self):
        """Cancel all open orders."""
        return self._delete("/orders/all")

    def _get_product_id(self, symbol):
        """Get product ID from symbol."""
        if not hasattr(self, "_product_cache"):
            self._product_cache = {}
        if symbol not in self._product_cache:
            products = self.get_products()
            for p in products:
                self._product_cache[p["symbol"]] = p["id"]
        return self._product_cache.get(symbol)


# ===================================================================
#  SIGNAL DETECTOR
# ===================================================================
class ArbitrageDetector:
    """Detects put-call parity arbitrage signals."""

    def __init__(self, contract_size=CONTRACT_SIZE):
        self.contract_size = contract_size

    def calculate_costs(self, future_price):
        """Calculate total costs per 1 BTC for a 3-leg arb trade."""
        fee_options  = future_price * (TAKER_FEE_OPTION_BPS / 1e4) * 2
        fee_future   = future_price * (TAKER_FEE_FUTURE_BPS / 1e4) * 1
        slip_options = SLIPPAGE_USD_OPTION * 2
        slip_future  = SLIPPAGE_USD_FUTURE * 1

        return {
            "total_fees":     fee_options + fee_future,
            "total_slippage": slip_options + slip_future,
            "total_cost":     fee_options + fee_future + slip_options + slip_future,
        }

    def detect(self, call_price, put_price, future_price, strike):
        """
        Detect conversion/reversal signal.

        Returns dict with signal details, or None if not tradable.
        """
        # Put-Call Parity gap
        gap = (call_price - put_price) - (future_price - strike)
        gross_edge = abs(gap)

        # Costs per 1 BTC
        costs = self.calculate_costs(future_price)
        net_edge_per_btc = gross_edge - costs["total_cost"]

        # Scale to contract size
        net_edge = net_edge_per_btc * self.contract_size
        gross_edge_scaled = gross_edge * self.contract_size
        fees_scaled = costs["total_fees"] * self.contract_size
        slip_scaled = costs["total_slippage"] * self.contract_size

        # Strategy direction
        if gap > 0:
            strategy = "reversal"
            legs = {
                "call": "SELL",
                "put":  "BUY",
                "future": "BUY",
            }
        else:
            strategy = "conversion"
            legs = {
                "call": "BUY",
                "put":  "SELL",
                "future": "SELL",
            }

        signal = {
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "strategy":       strategy,
            "call_price":     call_price,
            "put_price":      put_price,
            "future_price":   future_price,
            "strike":         strike,
            "raw_gap":        round(gap, 4),
            "gross_edge_usd": round(gross_edge_scaled, 6),
            "fees_usd":       round(fees_scaled, 6),
            "slippage_usd":   round(slip_scaled, 6),
            "net_edge_usd":   round(net_edge, 6),
            "net_edge_inr":   round(net_edge * USD_TO_INR, 4),
            "tradable":       net_edge > MIN_NET_EDGE_USD,
            "legs":           legs,
            "contract_size":  self.contract_size,
        }

        return signal


# ===================================================================
#  TRADE LOGGER
# ===================================================================
class TradeLogger:
    """Logs all signals and trades to CSV."""

    def __init__(self, filepath=LOG_FILE):
        self.filepath = filepath
        self._init_file()

    def _init_file(self):
        """Create log file with headers if it doesn't exist."""
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "mode", "strategy", "tradable",
                    "call_price", "put_price", "future_price", "strike",
                    "raw_gap", "gross_edge_usd", "fees_usd", "slippage_usd",
                    "net_edge_usd", "net_edge_inr", "action_taken",
                    "call_side", "put_side", "future_side",
                ])

    def log_signal(self, signal, mode="dry_run", action="SKIPPED"):
        """Write a signal to the log."""
        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                signal["timestamp"], mode, signal["strategy"],
                signal["tradable"],
                signal["call_price"], signal["put_price"],
                signal["future_price"], signal["strike"],
                signal["raw_gap"], signal["gross_edge_usd"],
                signal["fees_usd"], signal["slippage_usd"],
                signal["net_edge_usd"], signal["net_edge_inr"],
                action,
                signal["legs"]["call"], signal["legs"]["put"],
                signal["legs"]["future"],
            ])


# ===================================================================
#  POSITION MANAGER
# ===================================================================
class PositionManager:
    """Tracks open positions and enforces risk limits."""

    def __init__(self, filepath=POS_FILE):
        self.filepath = filepath
        self.positions = []
        self.daily_trades = 0
        self.last_trade_time = None
        self.portfolio_inr = INITIAL_MARGIN
        self._load()

    def _load(self):
        """Load positions from file."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    data = json.load(f)
                    self.positions = data.get("positions", [])
                    self.daily_trades = data.get("daily_trades", 0)
                    self.portfolio_inr = data.get("portfolio_inr", INITIAL_MARGIN)
            except (json.JSONDecodeError, KeyError):
                pass

    def _save(self):
        """Save positions to file."""
        with open(self.filepath, "w") as f:
            json.dump({
                "positions": self.positions,
                "daily_trades": self.daily_trades,
                "portfolio_inr": self.portfolio_inr,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

    def can_trade(self):
        """Check if we're allowed to open a new trade."""
        # Max positions
        if len(self.positions) >= MAX_OPEN_POSITIONS:
            return False, "Max open positions reached"

        # Daily limit
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return False, "Daily trade limit reached"

        # Cooldown
        if self.last_trade_time:
            elapsed = (datetime.now(timezone.utc) - self.last_trade_time).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                remaining = int(COOLDOWN_SECONDS - elapsed)
                return False, f"Cooldown: {remaining}s remaining"

        # Portfolio stop-loss
        if self.portfolio_inr < STOP_LOSS_PORTFOLIO:
            return False, f"Portfolio below stop-loss (Rs.{self.portfolio_inr:.0f})"

        return True, "OK"

    def add_position(self, signal):
        """Record a new position."""
        position = {
            "id": len(self.positions) + 1,
            "opened_at": signal["timestamp"],
            "strategy": signal["strategy"],
            "legs": signal["legs"],
            "strike": signal["strike"],
            "entry_gap": signal["raw_gap"],
            "net_edge_usd": signal["net_edge_usd"],
            "net_edge_inr": signal["net_edge_inr"],
            "contract_size": signal["contract_size"],
            "status": "open",
        }
        self.positions.append(position)
        self.daily_trades += 1
        self.last_trade_time = datetime.now(timezone.utc)
        self.portfolio_inr += signal["net_edge_inr"]
        self._save()
        return position

    def reset_daily(self):
        """Reset daily trade counter (call at midnight UTC)."""
        self.daily_trades = 0
        self._save()

    def get_summary(self):
        """Get position summary."""
        return {
            "open_positions": len([p for p in self.positions if p["status"] == "open"]),
            "total_trades": len(self.positions),
            "daily_trades": self.daily_trades,
            "portfolio_inr": round(self.portfolio_inr, 2),
        }


# ===================================================================
#  SYMBOL RESOLVER
# ===================================================================
class SymbolResolver:
    """Finds the right option/future symbols on Delta Exchange."""

    def __init__(self, client):
        self.client = client
        self.call_symbol = None
        self.put_symbol = None
        self.future_symbol = None
        self.strike = None

    def resolve(self, target_strike=STRIKE):
        """Find matching call, put, future symbols."""
        print("[*] Resolving symbols on Delta Exchange...")

        products = self.client.get_products(
            contract_types="call_options,put_options,futures,perpetual_futures"
        )

        calls, puts, futures = [], [], []
        for p in products:
            sym = p.get("underlying_asset", {}).get("symbol", "")
            if sym != UNDERLYING or p.get("state") != "live":
                continue
            ct = p.get("contract_type", "")
            if ct == "call_options":
                calls.append(p)
            elif ct == "put_options":
                puts.append(p)
            elif ct in ("futures", "perpetual_futures"):
                futures.append(p)

        if not calls or not puts or not futures:
            raise RuntimeError(f"No live BTC options/futures. "
                             f"Found: {len(calls)} calls, {len(puts)} puts, {len(futures)} futures")

        # Pick best strike
        available_strikes = sorted(
            set(int(float(c.get("strike_price", 0))) for c in calls if c.get("strike_price"))
        )
        self.strike = min(available_strikes, key=lambda s: abs(s - target_strike))

        # Find symbols
        self.call_symbol = next(
            (p["symbol"] for p in calls if int(float(p.get("strike_price", 0))) == self.strike), None
        )
        self.put_symbol = next(
            (p["symbol"] for p in puts if int(float(p.get("strike_price", 0))) == self.strike), None
        )
        for f in futures:
            if "BTCUSDT" in f.get("symbol", ""):
                self.future_symbol = f["symbol"]
                break
        if not self.future_symbol and futures:
            self.future_symbol = futures[0]["symbol"]

        print(f"    Strike: {self.strike}")
        print(f"    Call:   {self.call_symbol}")
        print(f"    Put:    {self.put_symbol}")
        print(f"    Future: {self.future_symbol}")

        if not all([self.call_symbol, self.put_symbol, self.future_symbol]):
            raise RuntimeError("Could not resolve all symbols")

        return self.strike


# ===================================================================
#  PRICE FETCHER
# ===================================================================
class PriceFetcher:
    """Fetches latest prices from Delta Exchange."""

    def __init__(self, client, symbols):
        self.client = client
        self.symbols = symbols  # SymbolResolver instance

    def get_prices(self):
        """
        Get latest call, put, future prices.
        Returns (call_price, put_price, future_price) or None on error.
        """
        try:
            call_ticker  = self.client.get_ticker(self.symbols.call_symbol)
            put_ticker   = self.client.get_ticker(self.symbols.put_symbol)
            fut_ticker   = self.client.get_ticker(self.symbols.future_symbol)

            # Use mark_price or last_price
            call_price  = float(call_ticker.get("mark_price") or call_ticker.get("close") or 0)
            put_price   = float(put_ticker.get("mark_price") or put_ticker.get("close") or 0)
            fut_price   = float(fut_ticker.get("mark_price") or fut_ticker.get("close") or 0)

            if call_price <= 0 or put_price <= 0 or fut_price <= 0:
                return None

            return call_price, put_price, fut_price

        except Exception as e:
            print(f"    [!] Price fetch error: {e}")
            return None


# ===================================================================
#  ORDER EXECUTOR
# ===================================================================
class OrderExecutor:
    """Executes 3-leg arb trades on Delta Exchange."""

    def __init__(self, client, symbols, mode="dry_run"):
        self.client = client
        self.symbols = symbols
        self.mode = mode

    def execute(self, signal):
        """
        Execute the 3-leg arb trade.
        In dry_run mode, just logs. In live mode, places real orders.
        """
        legs = signal["legs"]
        size = signal["contract_size"]

        print(f"\n    === EXECUTING {signal['strategy'].upper()} ===")
        print(f"    Call ({self.symbols.call_symbol}): {legs['call']} {size} BTC")
        print(f"    Put  ({self.symbols.put_symbol}):  {legs['put']} {size} BTC")
        print(f"    Fut  ({self.symbols.future_symbol}): {legs['future']} {size} BTC")
        print(f"    Net Edge: ${signal['net_edge_usd']:.6f} (Rs.{signal['net_edge_inr']:.4f})")

        if self.mode == "dry_run":
            print("    [DRY RUN] No real orders placed.")
            return True

        # LIVE MODE — Place real orders
        try:
            # Execute all 3 legs as market IOC orders
            results = []

            # Leg 1: Call
            r1 = self.client.place_order(
                symbol=self.symbols.call_symbol,
                size=int(size * 10000),  # Delta uses contract units
                side=legs["call"].lower(),
                order_type="market",
                time_in_force="ioc",
            )
            results.append(("Call", r1))

            # Leg 2: Put
            r2 = self.client.place_order(
                symbol=self.symbols.put_symbol,
                size=int(size * 10000),
                side=legs["put"].lower(),
                order_type="market",
                time_in_force="ioc",
            )
            results.append(("Put", r2))

            # Leg 3: Future
            r3 = self.client.place_order(
                symbol=self.symbols.future_symbol,
                size=int(size * 10000),
                side=legs["future"].lower(),
                order_type="market",
                time_in_force="ioc",
            )
            results.append(("Future", r3))

            # Check fills
            all_filled = True
            for leg_name, result in results:
                status = result.get("result", {}).get("state", "unknown")
                print(f"    {leg_name}: {status}")
                if status not in ("closed", "filled"):
                    all_filled = False

            if not all_filled:
                print("    [WARNING] Not all legs filled! Check positions manually.")

            return all_filled

        except Exception as e:
            print(f"    [ERROR] Order execution failed: {e}")
            return False


# ===================================================================
#  MAIN ALGO LOOP
# ===================================================================
def run_algo(mode="dry_run"):
    """Main algorithm loop."""

    print("=" * 65)
    print("  BTC OPTIONS ARBITRAGE — LIVE ALGORITHM")
    print(f"  Mode: {mode.upper()}")
    print(f"  Margin: Rs.{INITIAL_MARGIN:,} | Contract: {CONTRACT_SIZE} BTC")
    print(f"  Scan interval: {SCAN_INTERVAL_SECS}s")
    print("=" * 65)

    if mode == "live" and (not API_KEY or not API_SECRET):
        print("\n[ERROR] API_KEY and API_SECRET must be set for live mode!")
        print("  Edit live_algo.py and paste your Delta Exchange API credentials.")
        return

    # Initialize components
    client   = DeltaExchangeClient(API_KEY, API_SECRET)
    symbols  = SymbolResolver(client)
    detector = ArbitrageDetector(CONTRACT_SIZE)
    logger   = TradeLogger()
    pos_mgr  = PositionManager()

    # Resolve symbols
    try:
        strike = symbols.resolve(STRIKE)
    except Exception as e:
        print(f"[ERROR] Symbol resolution failed: {e}")
        return

    fetcher  = PriceFetcher(client, symbols)
    executor = OrderExecutor(client, symbols, mode)

    # Stats
    scan_count = 0
    signal_count = 0
    trade_count = 0
    last_day = datetime.now(timezone.utc).date()

    print(f"\n[*] Algorithm started. Scanning every {SCAN_INTERVAL_SECS}s...")
    print(f"    Press Ctrl+C to stop.\n")
    
    send_telegram_message(f"🚀 <b>BTC ARB ALGO STARTED</b> 🚀\nMode: {mode.upper()}\nStrike: {strike}\nPortfolio: Rs.{INITIAL_MARGIN}")

    try:
        while True:
            scan_count += 1
            now = datetime.now(timezone.utc)

            # Reset daily counter at midnight UTC
            if now.date() != last_day:
                pos_mgr.reset_daily()
                last_day = now.date()
                print(f"\n    [i] New day: {now.date()} — daily counter reset")

            # Fetch prices
            prices = fetcher.get_prices()
            if prices is None:
                print(f"  [{now.strftime('%H:%M:%S')}] Scan #{scan_count}: No prices available")
                time.sleep(SCAN_INTERVAL_SECS)
                continue

            call_price, put_price, fut_price = prices

            # Detect signal
            signal = detector.detect(call_price, put_price, fut_price, strike)
            gap_str = f"gap={signal['raw_gap']:+.2f}"
            edge_str = f"edge=Rs.{signal['net_edge_inr']:.4f}"

            if signal["tradable"]:
                signal_count += 1
                marker = ">>>"
                color_marker = signal["strategy"].upper()

                # Check risk limits
                can_trade, reason = pos_mgr.can_trade()

                if can_trade:
                    # Execute trade
                    success = executor.execute(signal)
                    if success:
                        trade_count += 1
                        pos_mgr.add_position(signal)
                        logger.log_signal(signal, mode, "TRADED")
                        summary = pos_mgr.get_summary()
                        
                        # Calculate margin used
                        notional_usd = signal['future_price'] * signal['contract_size']
                        margin_used_usd = notional_usd / LEVERAGE
                        margin_used_inr = margin_used_usd * USD_TO_INR

                        msg = (f"🚨 <b>ARB EXECUTED ({mode.upper()})</b> 🚨\n\n"
                               f"<b>Strategy:</b> {signal['strategy'].upper()}\n"
                               f"<b>Status:</b> 💰 Profit Locked In\n"
                               f"<b>Net Profit Booked:</b> Rs.{signal['net_edge_inr']:.2f}\n"
                               f"<b>Gap Found:</b> Rs.{signal['raw_gap']*USD_TO_INR:+.2f}\n\n"
                               f"<b>Margin Used:</b> Rs.{margin_used_inr:.2f} ({LEVERAGE}x Leverage)\n"
                               f"<b>Contract Size:</b> {signal['contract_size']} BTC\n\n"
                               f"<b>--- Trade Details ---</b>\n"
                               f"Call: {signal['legs']['call']} @ {signal['call_price']}\n"
                               f"Put: {signal['legs']['put']} @ {signal['put_price']}\n"
                               f"Future: {signal['legs']['future']} @ {signal['future_price']}\n\n"
                               f"<b>Updated Portfolio:</b> Rs.{summary['portfolio_inr']:,.2f}")
                        send_telegram_message(msg)
                        
                        print(f"    Portfolio: Rs.{summary['portfolio_inr']:,.2f} | "
                              f"Trades today: {summary['daily_trades']}/{MAX_TRADES_PER_DAY}")
                    else:
                        logger.log_signal(signal, mode, "EXEC_FAILED")
                else:
                    logger.log_signal(signal, mode, f"BLOCKED: {reason}")
                    msg = (f"⚠️ <b>ARB SIGNAL BLOCKED</b>\n"
                           f"Strategy: {signal['strategy'].upper()}\n"
                           f"Net Edge: Rs.{signal['net_edge_inr']:.2f}\n"
                           f"Reason: {reason}")
                    send_telegram_message(msg)
                    print(f"  [{now.strftime('%H:%M:%S')}] {marker} {color_marker} "
                          f"{gap_str} {edge_str} | BLOCKED: {reason}")
            else:
                # Log non-tradable signals (every 10th scan to reduce noise)
                if scan_count % 10 == 0:
                    logger.log_signal(signal, mode, "SKIPPED")
                    print(f"  [{now.strftime('%H:%M:%S')}] Scan #{scan_count}: "
                          f"C={call_price:.0f} P={put_price:.0f} F={fut_price:.0f} "
                          f"{gap_str} {edge_str} (below threshold)")

            # Wait for next scan
            time.sleep(SCAN_INTERVAL_SECS)

    except KeyboardInterrupt:
        print("\n\n[*] Algorithm stopped by user.")
        summary = pos_mgr.get_summary()
        print(f"\n  Session Summary:")
        print(f"  Scans:       {scan_count}")
        print(f"  Signals:     {signal_count}")
        print(f"  Trades:      {trade_count}")
        print(f"  Portfolio:   Rs.{summary['portfolio_inr']:,.2f}")
        print(f"  Log file:    {LOG_FILE}")
        print(f"  Positions:   {POS_FILE}")


# ===================================================================
#  ENTRY POINT
# ===================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTC Options Arbitrage Live Algorithm")
    parser.add_argument(
        "--mode",
        choices=["dry_run", "live"],
        default="dry_run" if os.getenv("DRY_RUN", "true").lower() == "true" else "live",
        help="Trading mode: dry_run or live (default is pulled from .env DRY_RUN)",
    )
    args = parser.parse_args()

    if args.mode == "live":
        print("\n" + "!" * 65)
        print("  WARNING: LIVE TRADING MODE")
        print("  Real orders will be placed on Delta Exchange!")
        print("  Press Ctrl+C within 5 seconds to cancel...")
        print("!" * 65)
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            sys.exit(0)

    run_algo(mode=args.mode)
