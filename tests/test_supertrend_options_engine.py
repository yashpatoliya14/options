from datetime import datetime, timedelta, timezone

import pandas as pd

from engine import Signal, StrategyEngine, StrategyParams


def _candles(closes):
    start = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
    rows = []
    for i, close in enumerate(closes):
        timestamp = start + timedelta(hours=4 * i)
        rows.append(
            {
                "timestamp": timestamp,
                "open": close,
                "high": close + 1.5,
                "low": close - 1.5,
                "close": close,
                "volume": 100,
            }
        )
    return pd.DataFrame(rows)


def test_supertrend_signal_flips_after_completed_bar():
    params = StrategyParams(
        underlying="BTC",
        candle_symbol="BTCUSDT",
        resolution="4h",
        supertrend_atr_period=15,
        supertrend_multiplier=1.5,
        spread_type="directional",
        trend_filter_enabled=False,
    )
    engine = StrategyEngine(params)

    prices = [100.0] * 20 + [130.0] * 25 + [50.0] * 2
    candles = _candles(prices)

    signal = engine.detect_signal(candles)

    assert signal is not None
    assert signal.signal_type in {"buy", "sell"}
    assert signal.timestamp == candles["timestamp"].iloc[-1]


def test_expiry_selection_uses_next_valid_expiry_after_cutoff():
    params = StrategyParams(spread_type="directional")
    engine = StrategyEngine(params)

    signal = Signal(
        timestamp=datetime(2026, 1, 2, 11, tzinfo=timezone.utc),
        direction="bull",
        fast_ema=0.0,
        slow_ema=0.0,
        signal_type="buy",
    )

    assert engine.get_expiry_type(signal.timestamp) == "next_day"


def test_bear_call_spread_sells_lower_call_and_buys_higher_call():
    params = StrategyParams(spread_type="directional", spread_width=100.0)
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
            {"symbol": "C-BTC-700-2026-01-04", "option_type": "call", "strike": 700.0, "mark": 300.0},
            {"symbol": "C-BTC-800-2026-01-04", "option_type": "call", "strike": 800.0, "mark": 200.0},
            {"symbol": "C-BTC-900-2026-01-04", "option_type": "call", "strike": 900.0, "mark": 100.0},
        ]
    )

    candidate = engine._build_directional_spread(signal, "2026-01-04", chain, "next_day")

    assert candidate is not None
    assert candidate.short_leg.option_type == "call"
    assert candidate.short_leg.strike < candidate.long_leg.strike
    assert candidate.long_leg.strike == 900.0
    assert candidate.short_leg.strike == 800.0
