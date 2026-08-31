from datetime import datetime, timezone

import pandas as pd

from engine import Signal, StrategyEngine, StrategyParams


def _signal(direction):
    return Signal(
        timestamp=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        direction=direction,
        fast_ema=101.0,
        slow_ema=100.0,
        adx=None,
    )


def _chain(expiry, option_type, rows, spot=1000):
    return pd.DataFrame(
        [
            {
                "symbol": f"{option_type}-{strike}-{expiry}",
                "option_type": option_type,
                "strike": strike,
                "mark": mark,
                "underlying_price": spot,
                "product_id": i,
            }
            for i, (strike, mark) in enumerate(rows, start=1)
        ]
    )


def test_zero_dte_in_band_wins_before_next_day():
    params = StrategyParams(credit_min=150, credit_max=200, spread_width=100, qty=3)
    engine = StrategyEngine(params)
    chains = {
        "2026-01-01": _chain("2026-01-01", "put", [(900, 250), (800, 70)]),
        "2026-01-02": _chain("2026-01-02", "put", [(900, 230), (800, 50)]),
    }

    candidate = engine.select_expiry_and_spread(_signal("bull"), chains)

    assert candidate is not None
    assert candidate.expiry == "2026-01-01"
    assert candidate.expiry_label == "0dte"
    assert candidate.short_leg.strike == 900
    assert candidate.long_leg.strike == 800
    assert candidate.net_credit == 180
    assert candidate.short_leg.qty == 3


def test_next_day_used_when_zero_dte_has_no_valid_credit():
    params = StrategyParams(credit_min=150, credit_max=200, spread_width=100)
    engine = StrategyEngine(params)
    chains = {
        "2026-01-01": _chain("2026-01-01", "call", [(1100, 260), (1200, 40)]),
        "2026-01-02": _chain("2026-01-02", "call", [(1100, 210), (1200, 40)]),
    }

    candidate = engine.select_expiry_and_spread(_signal("bear"), chains)

    assert candidate is not None
    assert candidate.expiry == "2026-01-02"
    assert candidate.expiry_label == "next_day"
    assert candidate.short_leg.strike == 1100
    assert candidate.long_leg.strike == 1200
    assert candidate.net_credit == 170


def test_none_when_no_expiry_has_credit_in_band():
    params = StrategyParams(credit_min=150, credit_max=200, spread_width=100)
    engine = StrategyEngine(params)
    chains = {
        "2026-01-01": _chain("2026-01-01", "put", [(900, 300), (800, 40)]),
        "2026-01-02": _chain("2026-01-02", "put", [(900, 100), (800, 20)]),
    }

    assert engine.select_expiry_and_spread(_signal("bull"), chains) is None
