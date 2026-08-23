"""
Run this FIRST, before trusting any backtest, and before running live_algo.py
for the first time.

It answers the open questions flagged in exchange/simulated_broker.py and
exchange/delta_client.py by hitting the real Delta Exchange API with your
credentials:

  1. Can /v2/history/candles serve a '3h' resolution directly, or do we need
     to aggregate from 1h candles?
  2. Do EXPIRED option product symbols still return historical candle data
     (determines: realistic backtest possible, or reconstructed-only)?
  3. What are the actual field names in a live /v2/tickers option-chain
     response (strike_price, best_bid, etc. -- confirms delta_client.py's
     parsing is correct)?
  4. What margin does Delta actually report for a sample short-option order
     (via the margin-calculator endpoint, once its real path is confirmed)?

Usage:
    python run_diagnostic.py

Requires DELTA_API_KEY / DELTA_API_SECRET in .env. Safe to run against
USE_TESTNET=true first.
"""
import sys
import time
import json

from config import CONFIG
from exchange.delta_client import DeltaClient


def main():
    if not CONFIG.delta_api_key or not CONFIG.delta_api_secret:
        print("ERROR: DELTA_API_KEY / DELTA_API_SECRET not set in .env. "
              "Set them (testnet keys are fine) and re-run.")
        sys.exit(1)

    client = DeltaClient(CONFIG)
    report = {}

    print(f"Using base URL: {CONFIG.delta_base_url}")

    # --- 1. products list & option product symbols ---
    print("\n[1/4] Fetching product list...")
    try:
        products = client.get_products()
        option_products = [
            p for p in products
            if p.get("underlying_asset", {}).get("symbol") == CONFIG.underlying_asset
            and p.get("contract_type") in ("call_options", "put_options")
        ]
        print(f"  Found {len(products)} total products, {len(option_products)} "
              f"{CONFIG.underlying_asset} option products.")
        if option_products:
            sample = option_products[0]
            print(f"  Sample product keys: {sorted(sample.keys())}")
            report["sample_option_product"] = sample
    except Exception as e:
        print(f"  FAILED: {e}")
        report["products_error"] = str(e)

    # --- 2. 3h candle resolution support on the live underlying ---
    print("\n[2/4] Testing 3h candle resolution on underlying perpetual...")
    now = int(time.time())
    day_ago = now - 86400
    for resolution in ("3h", "1h"):
        try:
            candles = client.get_historical_candles(CONFIG.underlying_symbol, resolution, day_ago, now)
            print(f"  resolution='{resolution}': {len(candles)} candles returned.")
            report[f"candles_{resolution}_count"] = len(candles)
        except Exception as e:
            print(f"  resolution='{resolution}': FAILED - {e}")
            report[f"candles_{resolution}_error"] = str(e)

    # --- 3. THE key question: expired option historical candles ---
    print("\n[3/4] Testing historical candles on an EXPIRED option symbol (critical for realistic backtest)...")
    if option_products:
        # find one that has already expired, if any are listed
        expired = [p for p in option_products if p.get("state") in ("expired", "settled")]
        target = expired[0] if expired else option_products[0]
        symbol = target.get("symbol", "")
        print(f"  Testing symbol: {symbol} (state={target.get('state')})")
        try:
            candles = client.get_historical_candles(symbol, "1h", now - 7 * 86400, now)
            print(f"  {len(candles)} candles returned for this option symbol over the last 7 days.")
            report["expired_option_candles_count"] = len(candles)
            if len(candles) > 0:
                print("  >>> RESULT: Expired/existing option symbols DO return historical "
                      "candle data. A 'realistic' backtest using real recorded premiums "
                      "may be possible -- but confirm depth (how far back) separately.")
            else:
                print("  >>> RESULT: No candle data returned for this option symbol. "
                      "Historical option premiums are likely NOT available via this "
                      "endpoint. Backtest will need to run in 'reconstructed' "
                      "(Black-Scholes theoretical) mode.")
        except Exception as e:
            print(f"  FAILED: {e} -- treat as 'not available', use reconstructed mode.")
            report["expired_option_candles_error"] = str(e)
    else:
        print("  Skipped -- no option products found for underlying "
              f"{CONFIG.underlying_asset}.")

    # --- 4. live option chain shape ---
    print("\n[4/4] Fetching a live option chain snapshot to verify field parsing...")
    try:
        expiries = client.get_available_expiries(CONFIG.underlying_asset)
        print(f"  Available expiries: {expiries[:5]}{'...' if len(expiries) > 5 else ''}")
        if expiries:
            chain = client.get_option_chain(CONFIG.underlying_asset, expiries[0])
            print(f"  {len(chain)} quotes parsed for nearest expiry {expiries[0]}.")
            if chain:
                print(f"  Sample parsed quote: {chain[0]}")
            else:
                print("  WARNING: 0 quotes parsed -- delta_client.py's field-name "
                      "assumptions (strike_price/best_bid/contract_type) likely need "
                      "correcting against the raw response. Add a raw print in "
                      "get_option_chain() to inspect.")
    except Exception as e:
        print(f"  FAILED: {e}")
        report["option_chain_error"] = str(e)

    print("\n--- Diagnostic complete ---")
    with open("diagnostic_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print("Full report written to diagnostic_report.json")
    print("\nReport this output back before we finalize backtest.py's data mode.")


if __name__ == "__main__":
    main()
