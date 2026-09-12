from datetime import datetime, timezone

import pandas as pd
import pytest

from engine import FillResult, Leg, SpreadCandidate, SpreadPosition, StrategyParams
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


class FakeEngine:
    def __init__(self, reason):
        self.reason = reason

    def should_close(self, position, mark):
        return self.reason

    def should_time_exit(self, position, now):
        return False

    def detect_crossover(self, candles):
        return None


class FakeExecutor:
    def __init__(self):
        self.close_calls = []

    def mark_to_market(self, position):
        return 100.0

    def close_spread(self, position):
        self.close_calls.append(position)
        return FillResult(
            ok=True,
            entry_or_exit_credit=100.0,
            slippage=0.0,
            commission=0.0,
            legs=[position.short_leg, position.long_leg],
        )


def _position():
    expiry = "2026-09-30"
    short = Leg("C-SHORT", "call", 100000, expiry, "sell", 1)
    long = Leg("C-LONG", "call", 100200, expiry, "buy", 1)
    candidate = SpreadCandidate("bear", expiry, "next_day", short, long, 200.0, 200.0)
    position = SpreadPosition.from_candidate(
        candidate,
        entry_time=datetime(2026, 9, 12, 11, tzinfo=timezone.utc),
        entry_credit=200.0,
    )
    object.__setattr__(position, "entry_mid", 200.0)
    return position


@pytest.mark.parametrize("reason", ["profit_target", "stop_loss"])
def test_live_runner_closes_position_for_tp_and_sl(tmp_path, reason):
    executor = FakeExecutor()
    runner = LiveRunner(
        engine=FakeEngine(reason),
        provider=FakeProvider(),
        executor=executor,
        params=StrategyParams(),
        state_path=str(tmp_path / "state.json"),
        trade_log_path=str(tmp_path / "trades.jsonl"),
    )
    runner.position = _position()

    runner.run_once()

    assert len(executor.close_calls) == 1
    assert runner.position is None
    trade = runner.trades if hasattr(runner, "trades") else None
    saved = (tmp_path / "trades.jsonl").read_text(encoding="utf-8")
    assert f'"exit_reason": "{reason}"' in saved
