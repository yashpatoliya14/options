# Dependency Map

This map reflects the current repository, not the older `PROJECT_CONTEXT.md` description.

The active strategy is Supertrend plus the EMA trend filter. Bullish entries use bull put credits; bearish entries use bear call credits (`sell` the lower call and `buy` the higher call). The screenshot-style higher-put sell/lower-put buy spread is supported as `put_credit`, but it is bullish/neutral and is not the active bearish structure. Directional candidates now select the available spread closest to the target `1.00:1` credit/max-loss ratio instead of rejecting every non-exact ratio. The latest corrected run produced 103 trades, 73 wins, 30 losses, and simulated P&L of `-$1,376.23`.

Lot sizing is controlled by `TRADE_QTY` in `.env.example`; live trading and the terminal backtest consume the value. The testnet demo intentionally stays at one lot for safety.

## Runtime Dependency Graph

```text
run_live.py
  -> live/config_live.yaml
  -> engine/StrategyParams, StrategyEngine
  -> live/DeltaRestClient, LiveDataProvider, LiveExecutor, LiveRunner

terminal_backtest.py
  -> local *_1h_cache.csv files (created/updated as cache)
  -> engine/StrategyParams, StrategyEngine
  -> backtest/BacktestRunner, HistoricalDataProvider, SimulatedClock, SimulatedExecutor

engine/
  -> pandas + Python standard library
  -> no exchange, network, sleep, or persistence dependency

backtest/
  -> engine/
  -> pandas
  -> Black-Scholes reconstruction in engine/

live/
  -> engine/
  -> requests
  -> live/config_live.yaml
  -> `.env` credentials at runtime

research/
  -> research/fast_sim.py
  -> BTCUSD_1h_cache.csv at runtime
  -> used by `python -m research.experiments ...`

tests/
  -> engine/, backtest/, live/, research/
  -> pytest fixtures and local test data
```

## Root Files

| File | Role | Keep? |
|---|---|---|
| `run_live.py` | Live trading entry point | Yes |
| `terminal_backtest.py` | Backtest entry point and market-data cache manager | Yes |
| `demo_trade.py` | Testnet open-and-close execution check | Keep if used operationally |
| `demo_trade_check.py` | Read-only testnet connectivity check | Keep if used operationally |
| `real_data_check.py` | Read-only production market-data check | Keep if used operationally |
| `verify_sl_cancel.py` | Exchange-stop cancellation verification; can place/close testnet or production trades depending on environment | Keep only with explicit operational purpose |
| `pnl_report.md` | Human report/output, not imported by code | Archive or remove only if no longer needed |
| `PROJECT_CONTEXT.md` | Older broad guide with stale file names and duplicated architecture | Candidate for archive/removal after review |
| `README.md` | Project overview; partially stale because it still describes the older EMA-first design | Update or replace, do not delete blindly |

## Data and Generated Files

The CSV cache files are ignored by `.gitignore` and are runtime inputs for backtests/research. They are not safe to call unused without checking whether local historical results depend on them.

The `.env` file is runtime secret configuration and must be retained locally, never committed.

## Cleanup Decision

No Python source file is proven unused from static references alone. The only strong redundancy is `PROJECT_CONTEXT.md`, which overlaps `doc/codebase.md` and contains stale references. Deleting it is intentionally left for explicit approval because file deletion is irreversible in this workflow.

Before removing any standalone script, confirm it is retired: scripts with no imports from other modules can still be valid command-line entry points.
