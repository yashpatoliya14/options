# Shared-Core EMA Credit-Spread Strategy

Python system for a BTC EMA crossover credit-spread strategy on Delta Exchange
India. The same pure `engine/StrategyEngine` is used by backtest and live
runners; data access, order placement, polling, persistence, and logs live
outside the engine.

## Architecture

```
engine/
  interfaces.py          Clock, DataProvider, OrderExecutor contracts
  models.py              Signal, legs, spreads, fills, TradeRecord, decisions
  config_schema.py       Shared StrategyParams schema
  strategy_engine.py     Pure EMA/ADX, spread selection, SL/TP/cooldown logic

backtest/
  historical_data_provider.py   Point-in-time candles + BS option reconstruction
  simulated_executor.py         Simulated spread fills and mark-to-market
  backtest_runner.py            Candle loop producing TradeRecord rows
  config_backtest.yaml          Backtest overlay

live/
  delta_rest.py              Delta India HMAC REST client
  live_data_provider.py         Delta REST market-data adapter
  live_executor.py              Delta REST spread order adapter
  live_runner.py                Poll loop, state file, JSONL trade log
  config_live.yaml              Testnet-first live overlay
```

`engine/` imports only stdlib and `pandas`. It does not import `requests`,
`websocket`, `exchange/`, file I/O, or sleep functions. Runners fetch candles
and option chains, call engine methods, execute orders, and persist results.

## Strategy

- Fast EMA crossing above slow EMA creates a `bull` signal and sells a put
  credit spread.
- Fast EMA crossing below slow EMA creates a `bear` signal and sells a call
  credit spread.
- Signals are calculated only from closed bars visible at the runner clock.
- Optional ADX filtering blocks weak crosses when `adx_min > 0`.
- Expiry selection tries 0DTE first, then next day.
- Spread selection uses the nearest OTM short strike with a long wing
  `spread_width` away, and accepts only net credit inside
  `[credit_min, credit_max]`.
- Opposite crossover while open closes the current spread; re-entry is still
  subject to cooldown.
- Profit target triggers when close debit is at or below `entry_credit *
  (1 - tp_pct)`.
- Stop loss triggers when close debit is at or above `entry_credit * sl_pct`.

## Historical Options

Delta does not provide historical option-chain snapshots for this strategy's
backtests. `backtest/HistoricalDataProvider` reconstructs option chains with
Black-Scholes from historical BTC candles, configured flat IV, and configured
risk-free rate.

This is theoretical data, not recorded market data. It does not model bid/ask
spreads, IV skew, liquidity, queue position, exchange outages, or real fill
quality. Every simulated trade is tagged with `data_mode: reconstructed` so
reports are not confused with live or recorded option-chain results.

## Configuration

`engine/config_schema.py` defines the shared `StrategyParams` dataclass:

- Strategy: `underlying`, `candle_symbol`, `resolution`, `ema_fast`,
  `ema_slow`, `adx_period`, `adx_min`, `credit_min`, `credit_max`,
  `spread_width`, `tp_pct`, `sl_pct`, `cooldown_seconds`, `qty`
- Costs: `slippage_pct`, `commission_per_leg`
- Reconstruction: `option_data_mode`, `assumed_iv`, `risk_free_rate`

Mode-specific YAML overlays live in:

- `backtest/config_backtest.yaml`
- `live/config_live.yaml`

Live defaults to Delta India testnet:

```
https://cdn-ind.testnet.deltaex.org
```

Production India REST can be set explicitly:

```
https://api.india.delta.exchange
```

API keys should be supplied from environment variables, not committed config.

## Development

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
pytest -q
```

Current coverage includes crossover detection, spread selection, stop/target
logic, cooldown behavior, engine parity, static no-I/O checks under `engine/`,
backtest trade-record output, and no-lookahead provider behavior.
