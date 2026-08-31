from datetime import datetime, timezone

import pandas as pd

from backtest import BacktestRunner, HistoricalDataProvider, SimulatedClock, SimulatedExecutor
from engine import Leg, Signal, SpreadCandidate, StrategyEngine, StrategyParams


class ScriptedEngine(StrategyEngine):
    def __init__(self, params):
        super().__init__(params)
        self._signals = [
            Signal(datetime(2026, 1, 1, 0, 4, tzinfo=timezone.utc), "bull", 2, 1),
        ]

    def detect_crossover(self, candles):
        if len(candles) < 5 or not self._signals:
            return None
        return self._signals.pop(0)

    def select_expiry_and_spread(self, signal, chain_by_expiry):
        expiry = next(iter(chain_by_expiry))
        short = Leg(f"P-BTC-9900-{expiry}", "put", 9900, expiry, "sell", self.params.qty)
        long = Leg(f"P-BTC-9700-{expiry}", "put", 9700, expiry, "buy", self.params.qty)
        return SpreadCandidate("bull", expiry, "0dte", short, long, 180, 200)

    def should_close(self, position, current_mark):
        return "profit_target"


def test_backtest_runner_emits_shared_reconstructed_trade_records():
    candles = pd.DataFrame(
        {
            "timestamp": pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=6, freq="min"),
            "open": [10000, 10010, 10020, 10030, 10040, 10050],
            "high": [10020, 10030, 10040, 10050, 10060, 10070],
            "low": [9990, 10000, 10010, 10020, 10030, 10040],
            "close": [10000, 10010, 10020, 10030, 10040, 10050],
            "volume": [1, 1, 1, 1, 1, 1],
        }
    )
    params = StrategyParams(
        ema_fast=2,
        ema_slow=4,
        spread_width=200,
        credit_min=1,
        credit_max=10000,
        option_data_mode="reconstructed",
    )
    clock = SimulatedClock(candles["timestamp"].iloc[0].to_pydatetime())
    provider = HistoricalDataProvider(candles, clock, params)
    executor = SimulatedExecutor(provider, params)
    runner = BacktestRunner(ScriptedEngine(params), provider, executor, params, lookback=10)

    result = runner.run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.data_mode == "reconstructed"
    assert trade.expiry_label == "0dte"
    assert trade.short_strike == 9900
    assert result.report["trade_count"] == 1
