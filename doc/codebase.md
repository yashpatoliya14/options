# Codebase Guide

## Purpose

This project is a BTC options trading system for Delta Exchange India. It supports both historical backtesting and live execution through one shared strategy engine.

## Main Flow

1. A data provider supplies closed BTC candles and option-chain data.
2. `engine/StrategyEngine` detects a signal, selects an expiry and spread, and manages exits.
3. A runner sends the decision to either a simulated or live executor.
4. The runner records fills, positions, and completed trades.

## Packages

- `engine/`: Pure strategy logic, models, indicators, option analytics, and configuration. It should not perform exchange I/O or persistence.
- `backtest/`: Historical candles, Black-Scholes option reconstruction, simulated fills, and the candle-by-candle backtest loop.
- `live/`: Delta REST client, live market data, real order execution, state persistence, and polling.
- `research/`: Experiments and faster simulation utilities used to investigate parameter choices.
- `tests/`: Behavioral, parity, no-lookahead, execution-safety, and strategy regression tests.

## Entry Points

- `terminal_backtest.py`: Run a backtest from the command line.
- `run_live.py`: Start the live runner, normally against the configured testnet or exchange endpoint.
- `demo_trade.py` and `demo_trade_check.py`: Manual/demo execution checks.
- `real_data_check.py` and `verify_sl_cancel.py`: Operational verification scripts.

## Configuration and Data

`engine/config_schema.py` defines `StrategyParams`. Mode-specific YAML overlays live in `backtest/config_backtest.yaml` and `live/config_live.yaml`. API credentials belong in environment variables, never in committed config.

Copy `.env.example` to `.env` and set `TRADE_QTY` for direct lot sizing. `TRADE_QTY=1` sends one contract per leg; the value is applied by `run_live.py` and `terminal_backtest.py`. The testnet demo intentionally stays at one lot for safety.

Backtests commonly reconstruct option chains with Black-Scholes rather than using recorded historical chains. Results are therefore theoretical and do not fully model historical liquidity, bid/ask behavior, queue position, or exchange outages.

## Useful Commands

```bash
pip install -r requirements.txt
pytest -q
```

When changing strategy behavior, update or add a focused test in `tests/` and verify both backtest and live runners still use the shared engine path.
