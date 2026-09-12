import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from engine import FillResult, Leg, SpreadCandidate, SpreadPosition, StrategyParams
from live.live_executor import LiveExecutor
from live.live_runner import LiveRunner


class FakeClock:
    def now(self):
        return datetime(2026, 9, 12, 12, tzinfo=timezone.utc)


class FakeProvider:
    clock = FakeClock()

    def get_candles(self, symbol, resolution, lookback):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def get_option_chain(self, underlying, expiry):
        return pd.DataFrame()
        
    def get_available_expiries(self, underlying):
        return []

    def get_quote(self, symbol):
        return {"mark": 10.0, "bid": 9.5, "ask": 10.5}


class FakeEngine:
    def __init__(self, close_reason=None):
        self.close_reason = close_reason

    def should_close(self, position, mark):
        return self.close_reason

    def should_time_exit(self, position, now):
        return False

    def detect_crossover(self, candles):
        return None


class FakeClient:
    def __init__(self, stop_order_state="open"):
        self.posts = []
        self.cancelled = []
        self.stop_order_state = stop_order_state
        self.get_order_calls = []

    def _post(self, path, body):
        self.posts.append((path, body))
        if path == "/v2/orders/bracket":
            return {"result": {"id": "stop-1"}}
        price = 12.0 if body["side"] == "sell" else 8.0
        return {"result": {"id": f"order-{len(self.posts)}", "avg_fill_price": price}}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"id": order_id}
        
    def get_order(self, order_id):
        self.get_order_calls.append(order_id)
        if order_id == "stop-1":
            return {"state": self.stop_order_state}
        return {"state": "open"}


def _position():
    expiry = "2026-09-30"
    short = Leg("C-BTC-SHORT", "call", 100000, expiry, "sell", 1)
    long = Leg("C-BTC-LONG", "call", 100200, expiry, "buy", 1)
    candidate = SpreadCandidate("bear", expiry, "next_day", short, long, 4000.0, 200.0)
    pos = SpreadPosition.from_candidate(
        candidate,
        datetime(2026, 9, 12, 11, tzinfo=timezone.utc),
        entry_credit=4000.0,
        stop_order_id="stop-1",
    )
    object.__setattr__(pos, "entry_mid", 4000.0)
    return pos


def test_tp_hit_cancels_stop_and_closes_legs(tmp_path):
    # Simulating TP being hit locally via engine.should_close
    client = FakeClient(stop_order_state="open")
    provider = FakeProvider()
    params = StrategyParams(use_exchange_stop=True)
    executor = LiveExecutor(client, provider, params)
    
    engine = FakeEngine(close_reason="profit_target")
    
    runner = LiveRunner(
        engine=engine,
        provider=provider,
        executor=executor,
        params=params,
        state_path=str(tmp_path / "state.json"),
        trade_log_path=str(tmp_path / "trades.jsonl"),
    )
    runner.position = _position()
    
    runner.run_once()
    
    # 1. The order should be cancelled before legs are closed
    assert "stop-1" in client.cancelled
    
    # 2. Position should be None after close
    assert runner.position is None
    
    # 3. Trade log should record profit_target
    saved = (tmp_path / "trades.jsonl").read_text(encoding="utf-8")
    assert '"exit_reason": "profit_target"' in saved


def test_sl_hit_on_exchange_watchdog_cleans_up(tmp_path):
    # Simulating SL hitting on the exchange (watchdog sees state as 'filled')
    client = FakeClient(stop_order_state="filled")
    provider = FakeProvider()
    params = StrategyParams(use_exchange_stop=True)
    executor = LiveExecutor(client, provider, params)
    
    # Engine does not locally close it
    engine = FakeEngine(close_reason=None)
    
    runner = LiveRunner(
        engine=engine,
        provider=provider,
        executor=executor,
        params=params,
        state_path=str(tmp_path / "state.json"),
        trade_log_path=str(tmp_path / "trades.jsonl"),
    )
    runner.position = _position()
    
    runner.run_once()
    
    # 1. The watchdog should detect 'filled' and call close_position("stop_loss")
    assert "stop-1" in client.get_order_calls
    
    # 2. close_spread will still try to cancel the stop (which is fine, just in case)
    assert "stop-1" in client.cancelled
    
    # 3. Position should be None after cleanup
    assert runner.position is None
    
    # 4. Trade log should record stop_loss
    saved = (tmp_path / "trades.jsonl").read_text(encoding="utf-8")
    assert '"exit_reason": "stop_loss"' in saved

def test_startup_orphan_repair_cancels_and_closes(tmp_path):
    # Simulating a crash and restart where the SL was hit while the bot was offline
    client = FakeClient(stop_order_state="filled")
    provider = FakeProvider()
    params = StrategyParams(use_exchange_stop=True)
    executor = LiveExecutor(client, provider, params)
    engine = FakeEngine(close_reason=None)
    
    # Write initial position to state file so it loads on startup
    state_path = tmp_path / "state.json"
    pos = _position()
    import dataclasses
    state_data = {
        "position": dataclasses.asdict(pos),
        "last_close_time": None
    }
    # Add extra fields needed by LiveRunner._load_position
    state_data["position"]["entry_time"] = pos.entry_time.isoformat()
    if getattr(pos, "detection_time", None):
        state_data["position"]["detection_time"] = pos.detection_time.isoformat()
    else:
        state_data["position"]["detection_time"] = None
        
    state_path.write_text(json.dumps(state_data), encoding="utf-8")
    
    runner = LiveRunner(
        engine=engine,
        provider=provider,
        executor=executor,
        params=params,
        state_path=str(state_path),
        trade_log_path=str(tmp_path / "trades.jsonl"),
    )
    
    # Position should have been cleared out during __init__ by startup repair
    assert runner.position is None
    assert "stop-1" in client.cancelled
