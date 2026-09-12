"""Place one small testnet spread and close it immediately.

Requires USE_TESTNET=true and testnet-only API credentials in .env.
This script never uses the production base URL.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from engine import Signal, SpreadPosition, StrategyEngine, StrategyParams
from live.delta_rest import DeltaRestClient
from live.live_data_provider import LiveDataProvider, WallClock
from live.live_executor import LiveExecutor


def main() -> int:
    load_dotenv()
    if os.getenv("USE_TESTNET", "false").lower() != "true":
        print("Refusing to trade: set USE_TESTNET=true")
        return 1

    api_key = os.getenv("DELTA_API_KEY", "")
    api_secret = os.getenv("DELTA_API_SECRET", "")
    if not api_key or not api_secret:
        print("Missing testnet API credentials")
        return 1

    config_path = Path(__file__).parent / "live" / "config_live.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config["qty"] = 1
    params = StrategyParams().overlay(config)
    client = DeltaRestClient(
        base_url=os.getenv("DELTA_TESTNET_URL", "https://cdn-ind.testnet.deltaex.org"),
        api_key=api_key,
        api_secret=api_secret,
    )
    clock = WallClock()
    provider = LiveDataProvider(client, clock)
    executor = LiveExecutor(client, provider, params)
    engine = StrategyEngine(params)

    now = clock.now()
    expiries = provider.get_available_expiries(params.underlying)
    chains = {
        expiry: provider.get_option_chain(params.underlying, expiry)
        for expiry in expiries[:2]
    }
    signal = Signal(now, "bull", 0.0, 0.0, signal_type="buy")
    candidate = engine.select_expiry_and_spread(signal, chains)
    if candidate is None:
        signal = Signal(now, "bear", 0.0, 0.0, signal_type="sell")
        candidate = engine.select_expiry_and_spread(signal, chains)
    if candidate is None:
        print("No valid testnet spread candidate was found; no order placed")
        return 1

    print(f"Candidate: {candidate.direction} {candidate.short_leg.symbol} / {candidate.long_leg.symbol}")
    position = None
    try:
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
        print(f"Opened: credit={fill.entry_or_exit_credit:.2f}, stop_id={stop_id}")
        position = SpreadPosition.from_candidate(
            candidate,
            entry_time=now,
            entry_credit=fill.entry_or_exit_credit,
            stop_order_id=stop_id,
            signal_type=signal.signal_type,
            expiry_type="same_day" if candidate.expiry == now.date().isoformat() else "next_day",
        )

        close = executor.close_spread(position)
        if not close.ok or close.entry_or_exit_credit is None:
            print(f"Close failed: {close.message}; verify the position and stop manually")
            return 2
        print(f"Closed: debit={close.entry_or_exit_credit:.2f}")
        print("Testnet demo trade completed and both legs were submitted for close.")
        return 0
    except Exception as exc:
        print(f"Demo trade error: {exc}")
        if position is not None:
            print("A position may remain; verify it in the Delta testnet account.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
