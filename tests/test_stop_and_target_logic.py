from datetime import datetime, timedelta, timezone

from engine import Leg, Signal, SpreadCandidate, SpreadPosition, StrategyEngine, StrategyParams


def _position(direction="bull", entry_credit=180):
    expiry = "2026-01-01"
    short = Leg("short", "put" if direction == "bull" else "call", 900, expiry, "sell", 1)
    long = Leg("long", "put" if direction == "bull" else "call", 800, expiry, "buy", 1)
    candidate = SpreadCandidate(direction, expiry, "0dte", short, long, 180, 100)
    return SpreadPosition.from_candidate(
        candidate,
        datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        entry_credit=entry_credit,
    )


def _signal(direction):
    return Signal(
        timestamp=datetime(2026, 1, 1, 13, tzinfo=timezone.utc),
        direction=direction,
        fast_ema=1,
        slow_ema=2,
    )


def test_profit_target_stop_loss_and_hold():
    engine = StrategyEngine(StrategyParams(tp_pct=0.50, sl_pct=2.0))
    position = _position(entry_credit=180)

    assert engine.should_close(position, current_mark=90) == "profit_target"
    assert engine.should_close(position, current_mark=360) == "stop_loss"
    assert engine.should_close(position, current_mark=150) is None


def test_cut_and_reenter_only_on_opposite_signal():
    engine = StrategyEngine(StrategyParams())
    position = _position(direction="bull")

    assert engine.should_cut_and_reenter(position, _signal("bear")) is True
    assert engine.should_cut_and_reenter(position, _signal("bull")) is False


def test_cooldown_true_until_elapsed():
    engine = StrategyEngine(StrategyParams(cooldown_seconds=600))
    closed_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    assert engine.apply_cooldown(closed_at, closed_at + timedelta(seconds=599)) is True
    assert engine.apply_cooldown(closed_at, closed_at + timedelta(seconds=600)) is False
