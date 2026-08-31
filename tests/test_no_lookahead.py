from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest import HistoricalDataProvider, SimulatedClock
from engine import StrategyParams


def _candles(closes):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return pd.DataFrame(
        {
            "timestamp": [start + timedelta(minutes=i) for i in range(len(closes))],
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "volume": [1] * len(closes),
        }
    )


def test_historical_provider_only_returns_bars_visible_at_simulated_now():
    clock = SimulatedClock(datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc))
    provider = HistoricalDataProvider(_candles([100, 101, 102, 999]), clock, StrategyParams())

    visible = provider.get_candles("BTCUSD", "5m", lookback=100)

    assert visible["close"].tolist() == [100, 101, 102]
    assert visible["timestamp"].max().to_pydatetime() == clock.now()


def test_option_chain_uses_spot_visible_at_simulated_now():
    clock = SimulatedClock(datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc))
    provider = HistoricalDataProvider(_candles([10000, 10100, 10200, 50000]), clock, StrategyParams())

    chain = provider.get_option_chain("BTC", "2026-01-01")

    assert chain["underlying_price"].nunique() == 1
    assert float(chain["underlying_price"].iloc[0]) == 10200.0
