"""
Verify SL order is cancelled after trade close.
Run this script to open a tiny test spread, wait 2 seconds, and then close it.
It will explicitly verify that the protective Stop Loss order on the exchange
was successfully cancelled.

Requires Delta API credentials in .env.
If you want to run on mainnet, make sure USE_TESTNET=false in your .env.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from engine import Signal, SpreadPosition, StrategyEngine, StrategyParams
from live.delta_rest import DeltaRestClient
from live.live_data_provider import LiveDataProvider, WallClock
from live.live_executor import LiveExecutor

def main() -> int:
    load_dotenv()
    use_testnet = os.getenv("USE_TESTNET", "false").lower() == "true"
    api_key = os.getenv("DELTA_API_KEY", "")
    api_secret = os.getenv("DELTA_API_SECRET", "")
    
    if not api_key or not api_secret:
        print("Missing DELTA_API_KEY or DELTA_API_SECRET in .env")
        return 1
        
    print(f"Running SL Cancel Verification on {'TESTNET' if use_testnet else 'MAINNET'}")

    config_path = Path(__file__).parent / "live" / "config_live.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    
    # Use minimum possible quantity for safety
    config["qty"] = 1
    # Ensure exchange stop is enabled
    config["use_exchange_stop"] = True
    
    # Relax constraints so it always finds a dummy spread
    config["credit_min"] = 0.0
    config["credit_max"] = 999999.0
    config["strike_selection"] = "atm_or_nearest_otm"
    config["liquidity_filter_enabled"] = False
    config["spread_width"] = 400.0

    params = StrategyParams().overlay(config)
    
    if use_testnet:
        base_url = os.getenv("DELTA_TESTNET_URL", "https://cdn-ind.testnet.deltaex.org")
    else:
        base_url = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
        
    client = DeltaRestClient(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
    )
    clock = WallClock()
    provider = LiveDataProvider(client, clock)
    executor = LiveExecutor(client, provider, params)
    engine = StrategyEngine(params)

    now = clock.now()
    try:
        expiries = provider.get_available_expiries(params.underlying)
        if not expiries:
            print("No expiries found.")
            return 1
    except Exception as e:
        print(f"Failed to fetch expiries. IP Whitelist issue? Error: {e}")
        return 1
        
    chains = {
        expiry: provider.get_option_chain(params.underlying, expiry)
        for expiry in expiries[:2]
    }
    
    # Pick the first expiry and extract the two highest call strikes to form a deep OTM bear call spread
    expiry = expiries[0]
    chain = chains[expiry]
    
    calls = chain[chain["option_type"] == "call"].sort_values("strike")
    if len(calls) < 2:
        print("Not enough call strikes available in the chain to form a spread.")
        return 1
        
    # Use the two highest strikes to minimize any risk of being ITM
    highest_calls = calls.tail(2)
    short_row = highest_calls.iloc[0]
    long_row = highest_calls.iloc[1]
    
    from engine import Leg, SpreadCandidate
    short_leg = Leg(
        symbol=short_row["symbol"],
        option_type="call",
        strike=float(short_row["strike"]),
        expiry=expiry,
        side="sell",
        qty=1,
    )
    long_leg = Leg(
        symbol=long_row["symbol"],
        option_type="call",
        strike=float(long_row["strike"]),
        expiry=expiry,
        side="buy",
        qty=1,
    )
    
    candidate = SpreadCandidate(
        direction="bear",
        expiry=expiry,
        expiry_label="dummy_test",
        short_leg=short_leg,
        long_leg=long_leg,
        net_credit=0.0,
        width=float(long_row["strike"] - short_row["strike"])
    )

    print(f"Selected Candidate: {candidate.direction} {candidate.short_leg.symbol} / {candidate.long_leg.symbol}")
    
    position = None
    stop_id = None
    try:
        print(">>> Opening spread and placing SL order...")
        fill = executor.open_spread(candidate.direction, candidate.short_leg, candidate.long_leg)
        if not fill.ok or fill.entry_or_exit_credit is None:
            print(f"Open failed: {fill.message}")
            return 1
            
        stop_id = fill.stop_order_id
        if stop_id is None:
            try:
                stop_id = json.loads(fill.message).get("stop_order_id")
            except (TypeError, ValueError, json.JSONDecodeError):
                stop_id = None
                
        if not stop_id:
            print("❌ Error: No stop order was returned from the exchange. Ensure stop_loss_pct > 0 and use_exchange_stop=True.")
            return 1
            
        print(f"✅ Spread Opened Successfully.")
        print(f"✅ Stop Loss Order Placed. ID: {stop_id}")
        
        position = SpreadPosition.from_candidate(
            candidate,
            entry_time=now,
            entry_credit=fill.entry_or_exit_credit,
            stop_order_id=stop_id,
            signal_type=signal.signal_type,
            expiry_type="same_day",
        )
        
        # Verify stop order exists on exchange
        order_status = client.get_order(stop_id)
        print(f"SL Order Status on Exchange before close: {order_status.get('state', 'unknown')}")

        print("\nWaiting 2 seconds before closing...\n")
        time.sleep(2)

        print(">>> Closing spread...")
        close = executor.close_spread(position)
        if not close.ok or close.entry_or_exit_credit is None:
            print(f"❌ Close failed: {close.message}")
            return 1
            
        print(f"✅ Spread Closed Successfully. Debit={close.entry_or_exit_credit:.2f}")
        
        print(f">>> Verifying SL Order {stop_id} was cancelled...")
        # Add a small delay for exchange state to update
        time.sleep(1)
        final_status = client.get_order(stop_id)
        state = final_status.get("state")
        
        if state == "cancelled":
            print(f"✅ SUCCESS! SL Order was successfully cancelled after trade close.")
        elif state == "filled":
            print(f"⚠️ SL Order was filled instead of cancelled.")
        else:
            print(f"❌ FAILURE! SL Order is still in state: '{state}'. You may need to manually cancel it.")
            
        return 0
        
    except Exception as exc:
        print(f"Error during verification: {exc}")
        if position is not None and stop_id is not None:
            print(f"⚠️ A position or SL ({stop_id}) may remain; verify it in your Delta account.")
        return 2

if __name__ == "__main__":
    sys.exit(main())
