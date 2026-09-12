from datetime import datetime, timezone

import pandas as pd

from engine import Signal, StrategyEngine, StrategyParams


def test_directional_credit_prefers_near_one_to_one_ratio():
    params = StrategyParams(
        spread_type="directional",
        spread_width=100.0,
        min_credit_risk_ratio=0.0,
        target_credit_risk_ratio=1.0,
    )
    engine = StrategyEngine(params)
    signal = Signal(
        timestamp=datetime(2026, 1, 2, 9, tzinfo=timezone.utc),
        direction="bull",
        fast_ema=0.0,
        slow_ema=0.0,
        signal_type="buy",
    )
    chain = pd.DataFrame(
        [
            {"symbol": "P-BTC-800", "option_type": "put", "strike": 800.0, "mark": 60.0, "underlying_price": 850.0},
            {"symbol": "P-BTC-700", "option_type": "put", "strike": 700.0, "mark": 20.0, "underlying_price": 850.0},
        ]
    )

    candidate = engine._build_directional_spread(signal, "2026-01-04", chain, "next_day")

    assert candidate is not None
    assert candidate.net_credit == 40


def test_bear_put_credit_sells_higher_put_and_buys_lower_put():
    params = StrategyParams(
        spread_type="directional",
        bear_structure="put_credit",
        spread_width=100.0,
        min_credit_risk_ratio=0.0,
    )
    engine = StrategyEngine(params)
    signal = Signal(
        timestamp=datetime(2026, 1, 2, 9, tzinfo=timezone.utc),
        direction="bear",
        fast_ema=0.0,
        slow_ema=0.0,
        signal_type="sell",
    )
    chain = pd.DataFrame(
        [
            {"symbol": "P-BTC-76000", "option_type": "put", "strike": 76000.0, "mark": 20.0},
            {"symbol": "P-BTC-77000", "option_type": "put", "strike": 77000.0, "mark": 60.0},
        ]
    )

    candidate = engine._build_directional_spread(signal, "2026-01-04", chain, "next_day")

    assert candidate is not None
    assert candidate.short_leg.option_type == "put"
    assert candidate.short_leg.side == "sell"
    assert candidate.long_leg.side == "buy"
    assert candidate.short_leg.strike == 77000.0
    assert candidate.long_leg.strike == 76000.0
