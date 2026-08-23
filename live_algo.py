"""
Delta Exchange - Live Production Bot
======================================================
Strategy: Short Strangle with 30% Stop-Loss (AlgoTest Classic)
    - Sells 10% OTM Call and Put weekly
    - 30% premium stop-loss per leg
    - Monitors every 60 seconds

Features:
    - Real Delta Exchange API integration (HMAC-SHA256 signed)
    - Option instrument discovery (auto-finds closest strike)
    - Real premium fetching from mark prices
    - State persistence (survives restarts)
    - Retry logic with exponential backoff
    - Graceful shutdown with signal handlers
    - Trade logging to CSV
    - Position reconciliation on startup
    - Configurable daily loss limit & kill-switch
    - Heartbeat Telegram alerts

REQUIREMENTS:
    pip install requests python-dotenv

USAGE (Linux/Azure):
    # DRY_RUN mode (default — uses real market data, simulates orders):
    nohup python live_algo.py > live_algo.log 2>&1 &

    # LIVE mode (places real orders — MAKE SURE API KEYS ARE SET):
    # Set MODE=LIVE in .env first
    nohup python live_algo.py > live_algo.log 2>&1 &
"""

import os
import sys
import time
import math
import json
import hmac
import hashlib
import signal
import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================================================================
# CONFIGURATION (all from .env)
# ============================================================================

# Execution Mode
MODE = os.getenv("MODE", "DRY_RUN").upper()
TRADING_SYMBOL = os.getenv("TRADING_SYMBOL", "BTCUSDT")

# Delta Exchange API
API_KEY = os.getenv("DELTA_API_KEY", "")
API_SECRET = os.getenv("DELTA_API_SECRET", "")
USE_TESTNET = os.getenv("USE_TESTNET", "false").lower() == "true"
BASE_URL = "https://testnet-api.delta.exchange/v2" if USE_TESTNET else "https://api.delta.exchange/v2"

# Telegram Alerts
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Strategy Parameters
OTM_PCT = float(os.getenv("OTM_PCT", "0.10"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.30"))
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "60"))
TRADE_FREQUENCY_DAYS = int(os.getenv("TRADE_FREQUENCY_DAYS", "7"))
CONTRACT_SIZE = int(os.getenv("CONTRACT_SIZE", "1"))

# Risk Management
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "0"))  # 0 = disabled
KILL_SWITCH_LOSS = float(os.getenv("KILL_SWITCH_LOSS", "0"))  # 0 = disabled

# Heartbeat (hours between "I'm alive" messages)
HEARTBEAT_INTERVAL_HOURS = int(os.getenv("HEARTBEAT_INTERVAL_HOURS", "6"))

# File paths (relative to script directory)
SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = SCRIPT_DIR / "bot_state.json"
TRADE_LOG_FILE = SCRIPT_DIR / "live_trades.csv"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(SCRIPT_DIR / "live_algo.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# GRACEFUL SHUTDOWN
# ============================================================================

shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    logger.warning(f"Received signal {signum}. Graceful shutdown initiated...")
    shutdown_requested = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ============================================================================
# TELEGRAM
# ============================================================================

def send_telegram(message):
    """Send alert to configured Telegram chat. Non-blocking, never raises."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


# ============================================================================
# DELTA EXCHANGE API CLIENT
# ============================================================================

class DeltaExchangeAPI:
    """Authenticated REST client for Delta Exchange v2 API."""
    
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
    
    def _sign(self, method, path, body=""):
        """Generate HMAC-SHA256 signature per Delta Exchange docs."""
        timestamp = str(int(time.time()))
        message = method + timestamp + path + body
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return {
            'api-key': self.api_key,
            'signature': signature,
            'timestamp': timestamp,
        }
    
    def _request(self, method, path, params=None, body=None, auth=False, retries=3):
        """Make an API request with retry logic."""
        url = self.base_url.rstrip('/v2') + path  # path already includes /v2/
        
        for attempt in range(retries):
            try:
                headers = {}
                body_str = ""
                
                if body is not None:
                    body_str = json.dumps(body, separators=(',', ':'))
                
                if auth:
                    query = ""
                    if params:
                        query = "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
                    headers = self._sign(method, path + query, body_str)
                
                if method == 'GET':
                    resp = self.session.get(url, params=params, headers=headers, timeout=15)
                elif method == 'POST':
                    resp = self.session.post(url, data=body_str, headers=headers, timeout=15)
                elif method == 'DELETE':
                    resp = self.session.delete(url, params=params, headers=headers, timeout=15)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                resp.raise_for_status()
                return resp.json()
                
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP {resp.status_code} on {method} {path}: {resp.text}")
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    wait = (2 ** attempt) * 2
                    logger.info(f"Retrying in {wait}s... (attempt {attempt + 1}/{retries})")
                    time.sleep(wait)
                    continue
                raise
            except requests.exceptions.ConnectionError as e:
                if attempt < retries - 1:
                    wait = (2 ** attempt) * 2
                    logger.warning(f"Connection error: {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise
            except Exception as e:
                logger.error(f"Request failed: {e}")
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                raise
        
        return None
    
    # --- Public Endpoints ---
    
    def get_ticker(self, symbol):
        """Fetch ticker data for a symbol."""
        resp = self._request('GET', '/v2/tickers', params={'symbol': symbol})
        if resp and resp.get('result'):
            # API can return list or single object
            result = resp['result']
            if isinstance(result, list):
                return result[0] if result else None
            return result
        return None
    
    def get_products(self, contract_types=None, states=None):
        """List available products/instruments."""
        params = {}
        if contract_types:
            params['contract_types'] = contract_types
        if states:
            params['states'] = states
        resp = self._request('GET', '/v2/products', params=params)
        if resp and resp.get('result'):
            return resp['result']
        return []
    
    def get_product_by_id(self, product_id):
        """Get a single product by ID."""
        resp = self._request('GET', f'/v2/products/{product_id}')
        if resp and resp.get('result'):
            return resp['result']
        return None
    
    # --- Authenticated Endpoints ---
    
    def get_positions(self, product_ids=None):
        """Get open positions."""
        params = {}
        if product_ids:
            params['product_ids'] = ','.join(str(pid) for pid in product_ids)
        resp = self._request('GET', '/v2/positions/margined', params=params, auth=True)
        if resp and resp.get('result'):
            return resp['result']
        return []
    
    def place_order(self, product_id, size, side, order_type="market_order", limit_price=None):
        """Place an order on Delta Exchange."""
        body = {
            "product_id": product_id,
            "size": size,
            "side": side,
            "order_type": order_type,
        }
        if limit_price is not None and order_type == "limit_order":
            body["limit_price"] = str(limit_price)
        
        resp = self._request('POST', '/v2/orders', body=body, auth=True)
        return resp
    
    def get_order(self, order_id):
        """Get order status by ID."""
        resp = self._request('GET', f'/v2/orders/{order_id}', auth=True)
        if resp and resp.get('result'):
            return resp['result']
        return None
    
    def cancel_all_orders(self, product_id=None):
        """Cancel all open orders, optionally filtered by product."""
        params = {}
        if product_id:
            params['product_id'] = product_id
        return self._request('DELETE', '/v2/orders/all', params=params, auth=True)
    
    def get_wallet(self):
        """Get wallet balances."""
        resp = self._request('GET', '/v2/wallet/balances', auth=True)
        if resp and resp.get('result'):
            return resp['result']
        return []


# ============================================================================
# OPTION INSTRUMENT DISCOVERY
# ============================================================================

def find_option_instruments(api, underlying_symbol, target_call_strike, target_put_strike):
    """
    Find the closest listed option instruments on Delta Exchange
    for the given target strikes.
    
    Returns: (call_product, put_product) dicts or (None, None) if not found.
    """
    logger.info(f"Discovering option instruments for {underlying_symbol}...")
    logger.info(f"  Target Call Strike: ${target_call_strike:,.0f}")
    logger.info(f"  Target Put Strike: ${target_put_strike:,.0f}")
    
    # Fetch all live call and put options
    calls = api.get_products(contract_types='call_options', states='live')
    puts = api.get_products(contract_types='put_options', states='live')
    
    if not calls and not puts:
        logger.error("No option instruments found on Delta Exchange.")
        return None, None
    
    # Filter for the underlying asset (BTC)
    asset_prefix = underlying_symbol.replace("USDT", "").replace("USD", "")  # "BTC"
    
    # Filter calls for this underlying
    relevant_calls = [p for p in calls if asset_prefix in p.get('symbol', '').upper()]
    relevant_puts = [p for p in puts if asset_prefix in p.get('symbol', '').upper()]
    
    logger.info(f"  Found {len(relevant_calls)} call options, {len(relevant_puts)} put options for {asset_prefix}")
    
    if not relevant_calls or not relevant_puts:
        logger.warning(f"No options found for {asset_prefix}. Available symbols: {[p.get('symbol') for p in (calls + puts)[:10]]}")
        return None, None
    
    # Find the closest strike for calls
    best_call = None
    best_call_diff = float('inf')
    for product in relevant_calls:
        strike = float(product.get('strike_price', 0))
        if strike <= 0:
            continue
        diff = abs(strike - target_call_strike)
        if diff < best_call_diff:
            best_call_diff = diff
            best_call = product
    
    # Find the closest strike for puts
    best_put = None
    best_put_diff = float('inf')
    for product in relevant_puts:
        strike = float(product.get('strike_price', 0))
        if strike <= 0:
            continue
        diff = abs(strike - target_put_strike)
        if diff < best_put_diff:
            best_put_diff = diff
            best_put = product
    
    if best_call:
        logger.info(f"  Selected Call: {best_call.get('symbol')} | Strike: ${float(best_call.get('strike_price', 0)):,.0f} | ID: {best_call.get('id')}")
    if best_put:
        logger.info(f"  Selected Put: {best_put.get('symbol')} | Strike: ${float(best_put.get('strike_price', 0)):,.0f} | ID: {best_put.get('id')}")
    
    return best_call, best_put


def get_option_mark_price(api, product_symbol):
    """Fetch the current mark price for an option instrument."""
    ticker = api.get_ticker(product_symbol)
    if ticker:
        mark = ticker.get('mark_price')
        if mark is not None:
            return float(mark)
    return None


# ============================================================================
# STATE PERSISTENCE
# ============================================================================

def save_state(state):
    """Save bot state to JSON file for crash recovery."""
    try:
        # Convert datetime objects to ISO strings
        serializable = {}
        for key, val in state.items():
            if isinstance(val, datetime):
                serializable[key] = val.isoformat()
            elif isinstance(val, dict):
                inner = {}
                for k, v in val.items():
                    inner[k] = v.isoformat() if isinstance(v, datetime) else v
                serializable[key] = inner
            else:
                serializable[key] = val
        
        with open(STATE_FILE, 'w') as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def load_state():
    """Load bot state from JSON file."""
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        
        # Convert ISO strings back to datetime
        for key in ['entry_time', 'expiry_time', 'last_heartbeat']:
            if key in state and state[key]:
                state[key] = datetime.fromisoformat(state[key])
        
        logger.info("Loaded saved state from previous session.")
        return state
    except Exception as e:
        logger.error(f"Failed to load state: {e}")
        return None


def clear_state():
    """Remove the state file."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


# ============================================================================
# TRADE LOGGING
# ============================================================================

def log_trade(trade_data):
    """Append a completed trade to the CSV log."""
    file_exists = TRADE_LOG_FILE.exists()
    
    fieldnames = [
        'timestamp', 'mode', 'symbol', 'action', 'side',
        'call_strike', 'put_strike', 'call_product_id', 'put_product_id',
        'call_premium', 'put_premium', 'total_premium',
        'exit_reason', 'entry_price', 'exit_price',
        'net_pnl', 'order_id', 'notes'
    ]
    
    try:
        with open(TRADE_LOG_FILE, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            
            # Fill missing fields with empty string
            row = {k: trade_data.get(k, '') for k in fieldnames}
            writer.writerow(row)
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")


# ============================================================================
# MAIN BOT LOGIC
# ============================================================================

def run_bot():
    """Main bot loop."""
    global shutdown_requested
    
    # Startup banner
    startup_msg = (
        f"🚀 <b>Options Algo Bot Starting</b>\n"
        f"Mode: <b>{MODE}</b>\n"
        f"Symbol: <b>{TRADING_SYMBOL}</b>\n"
        f"API: <b>{'Testnet' if USE_TESTNET else 'Production'}</b>\n"
        f"OTM: {OTM_PCT*100:.0f}% | SL: {STOP_LOSS_PCT*100:.0f}% | Contracts: {CONTRACT_SIZE}\n"
        f"Check Interval: {CHECK_INTERVAL_SEC}s | Frequency: {TRADE_FREQUENCY_DAYS}d"
    )
    logger.info(startup_msg.replace("<b>", "").replace("</b>", ""))
    send_telegram(startup_msg)
    
    # Validate credentials for LIVE mode
    if MODE == "LIVE":
        if not API_KEY or not API_SECRET or API_KEY == "your_api_key_here":
            err = "❌ CRITICAL: MODE=LIVE but Delta API keys are missing or placeholder in .env!"
            logger.error(err)
            send_telegram(err)
            return
    
    # Initialize API client
    api = DeltaExchangeAPI(API_KEY, API_SECRET, BASE_URL)
    
    # Test API connectivity
    try:
        ticker = api.get_ticker(TRADING_SYMBOL)
        if ticker:
            logger.info(f"API connected. {TRADING_SYMBOL} mark price: ${float(ticker.get('mark_price', 0)):,.2f}")
        else:
            logger.warning(f"Could not fetch ticker for {TRADING_SYMBOL}. API may be unavailable.")
    except Exception as e:
        logger.error(f"API connectivity test failed: {e}")
        if MODE == "LIVE":
            send_telegram(f"❌ API connectivity failed: {e}")
            return
    
    # Load or initialize state
    state = load_state() or {
        'active_call': None,        # {product_id, symbol, strike, entry_premium}
        'active_put': None,         # {product_id, symbol, strike, entry_premium}
        'entry_time': None,
        'expiry_time': None,
        'entry_price': None,        # underlying price at entry
        'daily_pnl': 0.0,
        'daily_pnl_date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'total_session_pnl': 0.0,
        'last_heartbeat': datetime.now(timezone.utc),
        'trades_today': 0,
    }
    
    # Position reconciliation on restart (LIVE mode only)
    if MODE == "LIVE" and state.get('active_call') or state.get('active_put'):
        logger.info("Reconciling positions from previous session...")
        product_ids = []
        if state.get('active_call'):
            product_ids.append(state['active_call']['product_id'])
        if state.get('active_put'):
            product_ids.append(state['active_put']['product_id'])
        
        try:
            positions = api.get_positions(product_ids)
            for pos in positions:
                size = int(pos.get('size', 0))
                pid = pos.get('product_id')
                if size == 0:
                    # Position was closed externally
                    if state.get('active_call') and state['active_call']['product_id'] == pid:
                        logger.warning(f"Call position {pid} no longer open. Clearing from state.")
                        state['active_call'] = None
                    if state.get('active_put') and state['active_put']['product_id'] == pid:
                        logger.warning(f"Put position {pid} no longer open. Clearing from state.")
                        state['active_put'] = None
            
            save_state(state)
            logger.info("Position reconciliation complete.")
        except Exception as e:
            logger.error(f"Position reconciliation failed: {e}")
            send_telegram(f"⚠️ Position reconciliation failed: {e}. Manual check recommended.")
    
    # ==========================================
    # MAIN LOOP
    # ==========================================
    
    while not shutdown_requested:
        try:
            # Reset daily P&L tracking at midnight UTC
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            if state.get('daily_pnl_date') != today:
                state['daily_pnl'] = 0.0
                state['daily_pnl_date'] = today
                state['trades_today'] = 0
                save_state(state)
            
            # Heartbeat
            now = datetime.now(timezone.utc)
            last_hb = state.get('last_heartbeat')
            if last_hb:
                if isinstance(last_hb, str):
                    last_hb = datetime.fromisoformat(last_hb)
                if (now - last_hb).total_seconds() >= HEARTBEAT_INTERVAL_HOURS * 3600:
                    hb_msg = (
                        f"💓 <b>Heartbeat ({MODE})</b>\n"
                        f"Bot alive at {now.strftime('%H:%M UTC')}\n"
                        f"Session P&L: ${state.get('total_session_pnl', 0):,.2f}\n"
                        f"Daily P&L: ${state.get('daily_pnl', 0):,.2f}\n"
                        f"Active Call: {'Yes' if state.get('active_call') else 'No'}\n"
                        f"Active Put: {'Yes' if state.get('active_put') else 'No'}"
                    )
                    logger.info(hb_msg.replace("<b>", "").replace("</b>", ""))
                    send_telegram(hb_msg)
                    state['last_heartbeat'] = now
                    save_state(state)
            
            # Fetch current underlying price
            ticker = api.get_ticker(TRADING_SYMBOL)
            if not ticker:
                logger.warning(f"Could not fetch {TRADING_SYMBOL} ticker. Retrying...")
                time.sleep(CHECK_INTERVAL_SEC)
                continue
            
            current_price = float(ticker.get('mark_price', 0))
            if current_price <= 0:
                logger.warning(f"Invalid mark price: {current_price}")
                time.sleep(CHECK_INTERVAL_SEC)
                continue
            
            # ==========================================
            # DAILY LOSS LIMIT CHECK
            # ==========================================
            if DAILY_LOSS_LIMIT > 0 and state.get('daily_pnl', 0) <= -DAILY_LOSS_LIMIT:
                msg = (
                    f"🛑 <b>DAILY LOSS LIMIT HIT ({MODE})</b>\n"
                    f"Daily P&L: ${state['daily_pnl']:,.2f}\n"
                    f"Limit: -${DAILY_LOSS_LIMIT:,.2f}\n"
                    f"No new trades until tomorrow."
                )
                logger.warning(msg.replace("<b>", "").replace("</b>", ""))
                send_telegram(msg)
                time.sleep(300)  # Check every 5 minutes
                continue
            
            # KILL SWITCH
            if KILL_SWITCH_LOSS > 0 and state.get('total_session_pnl', 0) <= -KILL_SWITCH_LOSS:
                msg = (
                    f"🚨 <b>KILL SWITCH ACTIVATED ({MODE})</b>\n"
                    f"Total Session P&L: ${state['total_session_pnl']:,.2f}\n"
                    f"Kill Switch: -${KILL_SWITCH_LOSS:,.2f}\n"
                    f"Bot shutting down. Manual intervention required."
                )
                logger.critical(msg.replace("<b>", "").replace("</b>", ""))
                send_telegram(msg)
                
                # Close all positions in LIVE mode
                if MODE == "LIVE":
                    _close_all_positions(api, state)
                
                shutdown_requested = True
                break
            
            # ==========================================
            # ENTER NEW STRANGLE
            # ==========================================
            if not state.get('active_call') and not state.get('active_put'):
                logger.info(f"No active positions. {TRADING_SYMBOL} at ${current_price:,.2f}. Entering new strangle...")
                
                # Calculate target strikes (10% OTM)
                target_call_strike = round(current_price * (1 + OTM_PCT), -2)
                target_put_strike = round(current_price * (1 - OTM_PCT), -2)
                
                # Discover option instruments
                call_product, put_product = find_option_instruments(
                    api, TRADING_SYMBOL, target_call_strike, target_put_strike
                )
                
                if not call_product or not put_product:
                    logger.error("Could not find suitable option instruments. Retrying in 5 minutes...")
                    time.sleep(300)
                    continue
                
                call_product_id = call_product['id']
                put_product_id = put_product['id']
                call_symbol = call_product.get('symbol', f"C-{target_call_strike}")
                put_symbol = put_product.get('symbol', f"P-{target_put_strike}")
                actual_call_strike = float(call_product.get('strike_price', target_call_strike))
                actual_put_strike = float(put_product.get('strike_price', target_put_strike))
                
                # Fetch real premiums
                call_premium = get_option_mark_price(api, call_symbol)
                put_premium = get_option_mark_price(api, put_symbol)
                
                if call_premium is None or put_premium is None:
                    logger.error(f"Could not fetch premiums. Call: {call_premium}, Put: {put_premium}. Retrying...")
                    time.sleep(60)
                    continue
                
                total_premium = call_premium + put_premium
                logger.info(f"  Call Premium: ${call_premium:,.2f} | Put Premium: ${put_premium:,.2f} | Total: ${total_premium:,.2f}")
                
                # Place orders
                call_order_id = None
                put_order_id = None
                
                if MODE == "LIVE":
                    try:
                        # Sell Call
                        call_resp = api.place_order(call_product_id, CONTRACT_SIZE, "sell", "market_order")
                        if call_resp and call_resp.get('result'):
                            call_order_id = call_resp['result'].get('id')
                            logger.info(f"  ✅ Call SELL order placed. ID: {call_order_id}")
                        else:
                            logger.error(f"  ❌ Call order failed: {call_resp}")
                            send_telegram(f"❌ Call order failed: {call_resp}")
                            continue
                        
                        # Sell Put
                        put_resp = api.place_order(put_product_id, CONTRACT_SIZE, "sell", "market_order")
                        if put_resp and put_resp.get('result'):
                            put_order_id = put_resp['result'].get('id')
                            logger.info(f"  ✅ Put SELL order placed. ID: {put_order_id}")
                        else:
                            logger.error(f"  ❌ Put order failed: {put_resp}")
                            send_telegram(f"❌ Put order failed. Call already placed (ID: {call_order_id}). Manual check needed!")
                            # Don't continue — call is already sold, track it
                    except Exception as e:
                        logger.error(f"Order placement error: {e}")
                        send_telegram(f"❌ Order error: {e}")
                        continue
                else:
                    logger.info(f"  [DRY RUN] Simulated SELL {CONTRACT_SIZE} Call ({call_symbol}) @ ${call_premium:,.2f}")
                    logger.info(f"  [DRY RUN] Simulated SELL {CONTRACT_SIZE} Put ({put_symbol}) @ ${put_premium:,.2f}")
                
                # Update state
                entry_time = datetime.now(timezone.utc)
                state['active_call'] = {
                    'product_id': call_product_id,
                    'symbol': call_symbol,
                    'strike': actual_call_strike,
                    'entry_premium': call_premium,
                    'order_id': call_order_id,
                }
                state['active_put'] = {
                    'product_id': put_product_id,
                    'symbol': put_symbol,
                    'strike': actual_put_strike,
                    'entry_premium': put_premium,
                    'order_id': put_order_id,
                }
                state['entry_time'] = entry_time
                state['expiry_time'] = entry_time + timedelta(days=TRADE_FREQUENCY_DAYS)
                state['entry_price'] = current_price
                save_state(state)
                
                # Log trade entry
                log_trade({
                    'timestamp': entry_time.isoformat(),
                    'mode': MODE,
                    'symbol': TRADING_SYMBOL,
                    'action': 'ENTRY',
                    'side': 'sell',
                    'call_strike': actual_call_strike,
                    'put_strike': actual_put_strike,
                    'call_product_id': call_product_id,
                    'put_product_id': put_product_id,
                    'call_premium': call_premium,
                    'put_premium': put_premium,
                    'total_premium': total_premium,
                    'entry_price': current_price,
                    'order_id': f"C:{call_order_id},P:{put_order_id}",
                })
                
                # Alert
                entry_msg = (
                    f"✅ <b>STRANGLE ENTERED ({MODE})</b>\n"
                    f"Underlying: {TRADING_SYMBOL} @ ${current_price:,.2f}\n"
                    f"Call: {call_symbol} (Strike: ${actual_call_strike:,.0f}) @ ${call_premium:,.2f}\n"
                    f"Put: {put_symbol} (Strike: ${actual_put_strike:,.0f}) @ ${put_premium:,.2f}\n"
                    f"Total Premium: ${total_premium:,.2f}\n"
                    f"Contracts: {CONTRACT_SIZE}\n"
                    f"Expiry: {state['expiry_time'].strftime('%Y-%m-%d %H:%M UTC')}"
                )
                logger.info(entry_msg.replace("<b>", "").replace("</b>", ""))
                send_telegram(entry_msg)
            
            # ==========================================
            # MONITOR ACTIVE POSITIONS
            # ==========================================
            else:
                expiry_time = state.get('expiry_time')
                if isinstance(expiry_time, str):
                    expiry_time = datetime.fromisoformat(expiry_time)
                
                # Check expiry
                if expiry_time and datetime.now(timezone.utc) >= expiry_time:
                    msg = (
                        f"⏱️ <b>STRANGLE EXPIRED ({MODE})</b>\n"
                        f"Options expired at {expiry_time.strftime('%Y-%m-%d %H:%M UTC')}\n"
                        f"Positions settled. New strangle will be entered on next cycle."
                    )
                    logger.info(msg.replace("<b>", "").replace("</b>", ""))
                    send_telegram(msg)
                    
                    # Estimate P&L at expiry
                    pnl = _estimate_expiry_pnl(state, current_price)
                    state['daily_pnl'] = state.get('daily_pnl', 0) + pnl
                    state['total_session_pnl'] = state.get('total_session_pnl', 0) + pnl
                    
                    log_trade({
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                        'mode': MODE,
                        'symbol': TRADING_SYMBOL,
                        'action': 'EXPIRY',
                        'side': 'close',
                        'call_strike': state['active_call']['strike'] if state.get('active_call') else '',
                        'put_strike': state['active_put']['strike'] if state.get('active_put') else '',
                        'exit_reason': 'Expiry',
                        'entry_price': state.get('entry_price', 0),
                        'exit_price': current_price,
                        'net_pnl': pnl,
                    })
                    
                    # Clear positions
                    state['active_call'] = None
                    state['active_put'] = None
                    state['entry_time'] = None
                    state['expiry_time'] = None
                    state['entry_price'] = None
                    save_state(state)
                    continue
                
                # ==========================================
                # STOP LOSS MONITORING
                # ==========================================
                
                # Monitor Call leg
                if state.get('active_call'):
                    call_info = state['active_call']
                    current_call_premium = get_option_mark_price(api, call_info['symbol'])
                    
                    if current_call_premium is None:
                        logger.warning(f"Could not fetch call premium for {call_info['symbol']}")
                    else:
                        sl_threshold = call_info['entry_premium'] * (1 + STOP_LOSS_PCT)
                        
                        if current_call_premium >= sl_threshold:
                            # STOP LOSS HIT — Close call
                            sl_msg = (
                                f"🛑 <b>CALL STOP LOSS HIT ({MODE})</b>\n"
                                f"Symbol: {call_info['symbol']}\n"
                                f"Entry Premium: ${call_info['entry_premium']:,.2f}\n"
                                f"Current Premium: ${current_call_premium:,.2f}\n"
                                f"SL Threshold: ${sl_threshold:,.2f}\n"
                                f"Loss: -${current_call_premium - call_info['entry_premium']:,.2f}"
                            )
                            logger.warning(sl_msg.replace("<b>", "").replace("</b>", ""))
                            send_telegram(sl_msg)
                            
                            # Place buy-to-close order
                            if MODE == "LIVE":
                                try:
                                    resp = api.place_order(call_info['product_id'], CONTRACT_SIZE, "buy", "market_order")
                                    if resp and resp.get('result'):
                                        logger.info(f"  ✅ Call buy-to-close executed. Order ID: {resp['result'].get('id')}")
                                    else:
                                        logger.error(f"  ❌ Call close failed: {resp}")
                                        send_telegram(f"❌ Call close order failed! Manual intervention needed!")
                                except Exception as e:
                                    logger.error(f"Call close error: {e}")
                                    send_telegram(f"❌ Call close error: {e}")
                            else:
                                logger.info(f"  [DRY RUN] Simulated BUY-TO-CLOSE Call @ ${current_call_premium:,.2f}")
                            
                            # Track P&L (premium collected - premium paid to close)
                            leg_pnl = call_info['entry_premium'] - current_call_premium
                            state['daily_pnl'] = state.get('daily_pnl', 0) + leg_pnl
                            state['total_session_pnl'] = state.get('total_session_pnl', 0) + leg_pnl
                            
                            log_trade({
                                'timestamp': datetime.now(timezone.utc).isoformat(),
                                'mode': MODE,
                                'symbol': TRADING_SYMBOL,
                                'action': 'CALL_SL_CLOSE',
                                'side': 'buy',
                                'call_strike': call_info['strike'],
                                'call_premium': current_call_premium,
                                'exit_reason': 'Call SL Hit',
                                'exit_price': current_price,
                                'net_pnl': leg_pnl,
                            })
                            
                            state['active_call'] = None
                            save_state(state)
                
                # Monitor Put leg
                if state.get('active_put'):
                    put_info = state['active_put']
                    current_put_premium = get_option_mark_price(api, put_info['symbol'])
                    
                    if current_put_premium is None:
                        logger.warning(f"Could not fetch put premium for {put_info['symbol']}")
                    else:
                        sl_threshold = put_info['entry_premium'] * (1 + STOP_LOSS_PCT)
                        
                        if current_put_premium >= sl_threshold:
                            # STOP LOSS HIT — Close put
                            sl_msg = (
                                f"🛑 <b>PUT STOP LOSS HIT ({MODE})</b>\n"
                                f"Symbol: {put_info['symbol']}\n"
                                f"Entry Premium: ${put_info['entry_premium']:,.2f}\n"
                                f"Current Premium: ${current_put_premium:,.2f}\n"
                                f"SL Threshold: ${sl_threshold:,.2f}\n"
                                f"Loss: -${current_put_premium - put_info['entry_premium']:,.2f}"
                            )
                            logger.warning(sl_msg.replace("<b>", "").replace("</b>", ""))
                            send_telegram(sl_msg)
                            
                            if MODE == "LIVE":
                                try:
                                    resp = api.place_order(put_info['product_id'], CONTRACT_SIZE, "buy", "market_order")
                                    if resp and resp.get('result'):
                                        logger.info(f"  ✅ Put buy-to-close executed. Order ID: {resp['result'].get('id')}")
                                    else:
                                        logger.error(f"  ❌ Put close failed: {resp}")
                                        send_telegram(f"❌ Put close order failed! Manual intervention needed!")
                                except Exception as e:
                                    logger.error(f"Put close error: {e}")
                                    send_telegram(f"❌ Put close error: {e}")
                            else:
                                logger.info(f"  [DRY RUN] Simulated BUY-TO-CLOSE Put @ ${current_put_premium:,.2f}")
                            
                            leg_pnl = put_info['entry_premium'] - current_put_premium
                            state['daily_pnl'] = state.get('daily_pnl', 0) + leg_pnl
                            state['total_session_pnl'] = state.get('total_session_pnl', 0) + leg_pnl
                            
                            log_trade({
                                'timestamp': datetime.now(timezone.utc).isoformat(),
                                'mode': MODE,
                                'symbol': TRADING_SYMBOL,
                                'action': 'PUT_SL_CLOSE',
                                'side': 'buy',
                                'put_strike': put_info['strike'],
                                'put_premium': current_put_premium,
                                'exit_reason': 'Put SL Hit',
                                'exit_price': current_price,
                                'net_pnl': leg_pnl,
                            })
                            
                            state['active_put'] = None
                            save_state(state)
                
                # If both legs closed (both SL hit), log and prepare for next entry
                if not state.get('active_call') and not state.get('active_put'):
                    msg = (
                        f"📊 <b>ALL LEGS CLOSED ({MODE})</b>\n"
                        f"Both positions closed. Waiting for next entry cycle.\n"
                        f"Daily P&L: ${state.get('daily_pnl', 0):,.2f}\n"
                        f"Session P&L: ${state.get('total_session_pnl', 0):,.2f}"
                    )
                    logger.info(msg.replace("<b>", "").replace("</b>", ""))
                    send_telegram(msg)
                    
                    state['entry_time'] = None
                    state['expiry_time'] = None
                    state['entry_price'] = None
                    save_state(state)
            
        except Exception as e:
            err_msg = f"❌ Unhandled error in main loop: {e}"
            logger.error(err_msg, exc_info=True)
            send_telegram(err_msg)
        
        # Sleep until next check
        time.sleep(CHECK_INTERVAL_SEC)
    
    # Graceful shutdown
    shutdown_msg = (
        f"🔴 <b>Bot Shutting Down ({MODE})</b>\n"
        f"Session P&L: ${state.get('total_session_pnl', 0):,.2f}\n"
        f"Active Call: {'Yes — manual close needed!' if state.get('active_call') else 'None'}\n"
        f"Active Put: {'Yes — manual close needed!' if state.get('active_put') else 'None'}"
    )
    logger.info(shutdown_msg.replace("<b>", "").replace("</b>", ""))
    send_telegram(shutdown_msg)
    save_state(state)
    logger.info("State saved. Goodbye.")


def _estimate_expiry_pnl(state, current_price):
    """Estimate P&L at expiry based on current price and entry premiums."""
    pnl = 0.0
    if state.get('active_call'):
        call = state['active_call']
        pnl += call['entry_premium']  # Premium collected
        intrinsic = max(0, current_price - call['strike'])
        pnl -= intrinsic  # Cash settled payout
    
    if state.get('active_put'):
        put = state['active_put']
        pnl += put['entry_premium']  # Premium collected
        intrinsic = max(0, put['strike'] - current_price)
        pnl -= intrinsic  # Cash settled payout
    
    return pnl


def _close_all_positions(api, state):
    """Emergency close all positions (kill switch)."""
    logger.warning("EMERGENCY: Closing all positions...")
    
    if state.get('active_call'):
        try:
            api.place_order(state['active_call']['product_id'], CONTRACT_SIZE, "buy", "market_order")
            logger.info("Emergency call close executed.")
        except Exception as e:
            logger.error(f"Emergency call close failed: {e}")
    
    if state.get('active_put'):
        try:
            api.place_order(state['active_put']['product_id'], CONTRACT_SIZE, "buy", "market_order")
            logger.info("Emergency put close executed.")
        except Exception as e:
            logger.error(f"Emergency put close failed: {e}")


if __name__ == "__main__":
    run_bot()
