from datetime import datetime, timedelta, timezone

import pandas as pd

from engine import StrategyEngine, StrategyParams


def _candles(closes, adx=30.0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        {
            "timestamp": [start + timedelta(minutes=i) for i in range(len(closes))],
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1] * len(closes),
            "adx": [adx] * len(closes),
        }
    )


def test_bullish_crossover_on_latest_closed_bar():
    params = StrategyParams(ema_fast=2, ema_slow=4)
    engine = StrategyEngine(params)

    signal = engine.detect_crossover(_candles([10, 9, 8, 7, 12]))

    assert signal is not None
    assert signal.direction == "bull"
    assert signal.fast_ema > signal.slow_ema


def test_bearish_crossover_on_latest_closed_bar():
    params = StrategyParams(ema_fast=2, ema_slow=4)
    engine = StrategyEngine(params)

    signal = engine.detect_crossover(_candles([10, 11, 12, 13, 8]))

    assert signal is not None
    assert signal.direction == "bear"
    assert signal.fast_ema < signal.slow_ema


def test_incomplete_last_bar_is_ignored():
    params = StrategyParams(ema_fast=2, ema_slow=4)
    engine = StrategyEngine(params)
    candles = _candles([10, 9, 8, 7, 12])
    candles["closed"] = [True, True, True, True, False]

    assert engine.detect_crossover(candles) is None


def test_adx_filter_blocks_weak_cross():
    params = StrategyParams(ema_fast=2, ema_slow=4, adx_min=25)
    engine = StrategyEngine(params)

    assert engine.detect_crossover(_candles([10, 9, 8, 7, 12], adx=10.0)) is None
