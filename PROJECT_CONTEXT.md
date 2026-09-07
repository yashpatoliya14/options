# PROJECT CONTEXT — BTC EMA Credit-Spread Algorithm

> **Read this file once.** It describes every folder, file, class, data flow, dependency, and design constraint in this codebase so that an AI or developer can pick up any task without re-reading the entire repo.

---

## 1. What This Project Does (TL;DR)

An automated **options credit-spread trading system** for **Bitcoin** on **Delta Exchange India**.

- A **fast EMA crosses above a slow EMA** → bullish signal → sell a **put credit spread** (receive premium).
- A **fast EMA crosses below a slow EMA** → bearish signal → sell a **call credit spread** (receive premium).
- The strategy aims to **collect and keep the premium** (options decay in the seller's favor).
- The same pure strategy engine powers both a **backtest** (historical data, Black-Scholes reconstruction) and **live trading** (real Delta Exchange API).

---

## 2. Project Directory Tree

```
.
├── .commandcode/taste/taste.md          # Empty; placeholder for code-taste rules
├── .env                                 # Secrets (gitignored): DELTA_API_KEY, DELTA_API_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TRADE_QTY
├── .gitignore                           # Ignores .env, __pycache__/, backtest_charts, pytest cache
├── pytest.ini                           # pytest config: testpaths = tests
├── requirements.txt                     # Python dependencies
├── README.md                            # Project README with architecture + strategy docs
│
├── fetch_data.py                        # Standalone script: fetches BTC 5m candles from Binance → data/BTCUSD_5m.csv
├── backtest.py                          # Entry point: runs full backtest on CSV, prints yearly/monthly stats
├── run_live.py                          # Entry point: runs live trading loop on Delta Exchange testnet
│
├── engine/                              # CORE: Pure strategy logic (NO I/O, NO exchange, NO sleep)
│   ├── __init__.py                      # Public API exports
│   ├── interfaces.py                    # Abstract contracts: Clock, DataProvider, OrderExecutor
│   ├── models.py                        # All data classes: Signal, Leg, SpreadCandidate, SpreadPosition, FillResult, TradeRecord, Decision
│   ├── config_schema.py                 # StrategyParams frozen dataclass with validation
│   └── strategy_engine.py              # Core engine: detect_crossover, select_expiry_and_spread, should_close, apply_cooldown
│
├── backtest/                            # BACKTEST MODE: Simulated data + execution
│   ├── __init__.py                      # Public API exports
│   ├── config_backtest.yaml             # Backtest-specific parameter overlay
│   ├── historical_data_provider.py      # SimulatedClock + HistoricalDataProvider (Black-Scholes option chain reconstruction)
│   ├── simulated_executor.py            # SimulatedExecutor: mock fills with slippage/commission
│   └── backtest_runner.py               # BacktestRunner: candle loop producing TradeRecord rows + BacktestResult
│
├── live/                                # LIVE MODE: Real Delta Exchange integration
│   ├── __init__.py                      # Public API exports
│   ├── config_live.yaml                 # Live-specific parameter overlay (testnet-first)
│   ├── delta_rest.py                    # DeltaRestClient: HMAC-signed REST API client for Delta Exchange India
│   ├── live_data_provider.py            # WallClock + LiveDataProvider: real market data from Delta REST
│   ├── live_executor.py                 # LiveExecutor: real order placement via Delta REST
│   └── live_runner.py                   # LiveRunner: poll loop, state persistence, trade log, Telegram notifications
│
├── data/                                # Historical market data
│   └── BTCUSD_5m.csv                    # ~1 year of BTC 5-minute candles from Binance
│
├── tests/                               # Test suite
│   ├── conftest.py                      # Adds project root to sys.path
│   ├── test_crossover_detection.py      # Tests EMA crossover detection (bull/bear, incomplete bars, ADX filter)
│   ├── test_spread_selection.py         # Tests expiry + spread selection (0dte vs next_day, credit band)
│   ├── test_stop_and_target_logic.py    # Tests profit target, stop loss, hold, cut-and-reenter, cooldown
│   ├── test_engine_parity.py            # Tests deterministic engine output + no I/O imports in engine/
│   ├── test_backtest_runner.py          # Tests backtest runner emits shared reconstructed TradeRecord
│   └── test_no_lookahead.py             # Tests that HistoricalDataProvider never leaks future data
│
├── backtest_output.txt                  # Backtest output log (v1) — UTF-16 encoded progress
├── backtest_output_v2.txt               # Backtest output log (v2)
└── backtest_output_v3.txt               # Backtest output log (v3)
```

---

## 3. Architecture Overview

### The Key Design Principle

**The `engine/` package is a pure library.** It imports only `pandas` and `stdlib`. It has:
- **Zero** network calls (`requests`, `websocket`)
- **Zero** file I/O (`open`, `pathlib`, `os`)
- **Zero** sleep or timing (`time`)
- **Zero** exchange-specific code

This is **enforced by `test_engine_parity.py`** which uses AST analysis to scan every `engine/*.py` file for banned imports and calls.

### How Backtest and Live Share the Engine

```
                    ┌─────────────────────┐
                    │     engine/         │
                    │  StrategyEngine     │
                    │  StrategyParams     │
                    │  Signal, Leg, etc.  │
                    └──────┬──────────────┘
                           │
              ┌────────────┼────────────────┐
              │                             │
     ┌────────▼────────┐         ┌──────────▼──────────┐
     │   backtest/     │         │      live/          │
     │  (Simulated)    │         │   (Real Exchange)   │
     │                 │         │                     │
     │ SimulatedClock  │         │ WallClock           │
     │ HistoricalDP    │         │ LiveDataProvider    │
     │ SimulatedExec   │         │ LiveExecutor        │
     │ BacktestRunner  │         │ LiveRunner          │
     └─────────────────┘         └─────────────────────┘
```

Both runners implement the **same loop**:
1. Get candles from DataProvider
2. If position open → check should_close() → maybe close
3. Detect crossover → if signal, check cooldown, select spread, open position

### Interface Contracts (engine/interfaces.py)

| Abstract Class | Method | Purpose |
|---|---|---|
| `Clock` | `now() → datetime` | Returns current time (simulated or real) |
| `DataProvider` | `get_candles(symbol, resolution, lookback) → DataFrame` | Returns OHLCV candles |
| `DataProvider` | `get_available_expiries(underlying) → list[str]` | Returns ISO date strings |
| `DataProvider` | `get_option_chain(underlying, expiry_date) → DataFrame` | Returns option chain with symbol, strike, bid, ask, mark |
| `DataProvider` | `get_quote(symbol) → dict` | Returns mark, bid, ask for a single symbol |
| `OrderExecutor` | `open_spread(direction, short_leg, long_leg) → FillResult` | Opens a credit spread |
| `OrderExecutor` | `close_spread(position) → FillResult` | Closes an existing spread |
| `OrderExecutor` | `mark_to_market(position) → float` | Returns current debit to close the spread |

---

## 4. Detailed File-by-File Reference

### 4.1 `engine/config_schema.py` — StrategyParams

A **frozen dataclass** with strict validation. All strategy configuration lives here.

**Fields (with defaults):**
| Field | Type | Default | Description |
|---|---|---|---|
| `underlying` | str | `"BTC"` | Underlying asset symbol |
| `candle_symbol` | str | `"BTCUSD"` | Symbol for candle data |
| `resolution` | str | `"5m"` | Candle resolution |
| `ema_fast` | int | `9` | Fast EMA period |
| `ema_slow` | int | `21` | Slow EMA period (must be > ema_fast) |
| `adx_period` | int | `14` | ADX calculation period |
| `adx_min` | float | `0.0` | Minimum ADX to accept signal (0 = disabled) |
| `credit_min` | float | `150.0` | Minimum net credit for spread |
| `credit_max` | float | `200.0` | Maximum net credit for spread |
| `spread_width` | float | `200.0` | Strike width between short and long legs |
| `tp_pct` | float | `0.50` | Profit target: close when mark ≤ entry * (1 - tp_pct) |
| `sl_pct` | float | `2.00` | Stop loss: close when mark ≥ entry * sl_pct |
| `cooldown_seconds` | int | `900` | Seconds to wait after closing before re-entry |
| `qty` | int | `1` | Number of contracts per leg |
| `slippage_pct` | float | `0.0` | Slippage applied to fills |
| `commission_per_leg` | float | `0.0` | Commission per leg |
| `option_data_mode` | str | `"reconstructed"` | Data mode tag for TradeRecord |
| `assumed_iv` | float | `0.55` | Implied volatility for BS reconstruction |
| `risk_free_rate` | float | `0.0` | Risk-free rate for BS reconstruction |

**Key method:** `overlay(values: dict) → StrategyParams` — creates a new params with values overwritten by a dict (used for YAML config loading).

### 4.2 `engine/models.py` — Data Classes

All models are **frozen dataclasses** (immutable).

| Class | Fields | Purpose |
|---|---|---|
| `Signal` | timestamp, direction ("bull"/"bear"), fast_ema, slow_ema, adx | EMA crossover signal |
| `Leg` | symbol, option_type ("put"/"call"), strike, expiry, side ("buy"/"sell"), qty, product_id | One option leg |
| `SpreadCandidate` | direction, expiry, expiry_label ("0dte"/"next_day"), short_leg, long_leg, net_credit, width | A proposed spread to open |
| `SpreadPosition` | all of SpreadCandidate + entry_time, entry_credit, qty, stop_order_id | An open position |
| `FillResult` | ok, entry_or_exit_credit, slippage, commission, legs, message | Result of open/close |
| `TradeRecord` | entry_time, exit_time, direction, expiry, expiry_label, short_strike, long_strike, credit_received, exit_reason, realized_pnl, slippage, commission, data_mode | Completed trade record |
| `Decision` | action ("open"/"close"/"hold"/"skip"), reason, signal, candidate, exit_reason | Engine decision (for logging) |

**Type aliases:** `Direction = Literal["bull", "bear"]`, `OptionType = Literal["put", "call"]`, `Side = Literal["buy", "sell"]`, `ExpiryLabel = Literal["0dte", "next_day"]`, `ExitReason = Literal["profit_target", "stop_loss", "signal_cut", "expired", "cooldown_skip"]`

**Key factory:** `SpreadPosition.from_candidate(candidate, entry_time, entry_credit, stop_order_id)` — creates a position from a candidate.

### 4.3 `engine/strategy_engine.py` — StrategyEngine

The core strategy logic class. Initialized with `StrategyParams`.

**Public methods:**

| Method | Signature | Description |
|---|---|---|
| `detect_crossover` | `(candles: DataFrame) → Signal \| None` | Checks last two closed bars for EMA cross. Filters by ADX if `adx_min > 0`. Returns None if no cross or ADX too low. |
| `select_expiry_and_spread` | `(signal, chain_by_expiry: dict[str, DataFrame]) → SpreadCandidate \| None` | Tries 0dte first, then next_day. For each expiry, finds nearest OTM short strike, calculates long strike `spread_width` away, checks net credit is in `[credit_min, credit_max]`. |
| `should_cut_and_reenter` | `(position, new_signal) → bool` | Returns True only if new signal direction differs from position direction. |
| `should_close` | `(position, current_mark) → "profit_target" \| "stop_loss" \| None` | Checks mark against TP/SL thresholds. |
| `apply_cooldown` | `(last_close_time, now) → bool` | Returns True if still in cooldown. |

**Private methods:**
- `_closed_candles(candles)` — filters to only closed bars (handles both `closed` and `is_closed` columns)
- `_latest_adx(candles)` — calculates ADX from candle data (or returns None)
- `_select_for_expiry(...)` — single-expiry spread selection logic
- `_price_series(chain)` — picks price from `mark`/`mid`/`premium`/`bid+ask`
- `_spot(chain)` — extracts spot price from chain
- `_leg(row, side, expiry, option_type)` — creates a Leg from a DataFrame row
- `_to_datetime(value)` — normalizes to UTC datetime

### 4.4 `backtest/historical_data_provider.py` — Simulated Data

**`SimulatedClock`:**
- Has `now()` and `set(value)` methods
- Used by BacktestRunner to advance through historical candles one by one

**`HistoricalDataProvider`:**
- Takes a DataFrame of all candles + a SimulatedClock + StrategyParams
- `get_candles()` — returns only candles with `timestamp ≤ clock.now()` (no lookahead)
- `get_available_expiries()` — returns today and tomorrow as ISO dates
- `get_option_chain()` — **reconstructs** option chains using Black-Scholes:
  - Calculates strike step as `max(round(spot * 0.01 / 50) * 50, 50)`
  - Generates 20 strikes on each side of spot
  - Uses `params.assumed_iv` (default 0.55) and `params.risk_free_rate`
  - Uses `statistics.NormalDist` for normal CDF
- `get_quote()` — re-parses a symbol and recalculates BS price

**No-lookahead guarantee:** The `_spot_as_of()` method uses `searchsorted` to find the last candle at or before `clock.now()`. The `get_candles()` method uses the same approach.

### 4.5 `backtest/simulated_executor.py` — SimulatedExecutor

- `open_spread()` — gets BS marks for both legs, applies slippage, returns FillResult
- `close_spread()` — gets current mark, applies slippage on the exit side, returns FillResult
- `mark_to_market()` — returns current short_mark - long_mark

### 4.6 `backtest/backtest_runner.py` — BacktestRunner

**The backtest loop (identical to live logic):**
1. For each candle in the dataset:
   - Advance `SimulatedClock` to candle timestamp
   - Get visible candles from provider
   - If position open: check `should_close()` → maybe close
   - Detect crossover → if signal:
     - If position open and same direction: skip
     - If position open and opposite direction: close (signal_cut)
     - Check cooldown
     - Get expiries, build chains, select spread
     - Open position
2. After all candles: close any remaining position with reason `"expired"`

**Output:** `BacktestResult` with `trades: list[TradeRecord]`, `report: dict` (trade_count, total_pnl, win_rate, max_drawdown, equity_curve, etc.)

### 4.7 `backtest/config_backtest.yaml` — Backtest Config

```yaml
ema_fast: 9
ema_slow: 21
adx_min: 0           # No ADX filter in backtest
credit_min: 150
credit_max: 200
spread_width: 200
tp_pct: 0.50
sl_pct: 2.00
cooldown_seconds: 900
slippage_pct: 0.0025  # 0.25% slippage
option_data_mode: reconstructed
assumed_iv: 0.55
```

### 4.8 `live/delta_rest.py` — DeltaRestClient

A **requests-based** REST client for Delta Exchange India.

- HMAC-SHA256 signed requests (api-key, signature, timestamp headers)
- Automatic retries with exponential backoff
- Methods: `get_historical_candles()`, `get_products()`, `get_ticker()`, `get_available_expiries()`, `get_option_chain()`

### 4.9 `live/live_data_provider.py` — LiveDataProvider + WallClock

- **WallClock:** `now()` returns `datetime.now(timezone.utc)`
- **LiveDataProvider:** Wraps DeltaRestClient to implement `DataProvider` interface
  - `get_candles()` — converts Delta candle format to standard OHLCV DataFrame
  - `get_available_expiries()` — filters out past expiries
  - `get_option_chain()` — converts Delta ticker format to standard chain DataFrame
  - `get_quote()` — returns bid/ask/mark/greeks from Delta ticker

### 4.10 `live/live_executor.py` — LiveExecutor

- `open_spread()` — places market orders for long leg (buy) then short leg (sell), places exchange stop order
- `close_spread()` — places reduce-only market orders to close both legs
- `mark_to_market()` — gets current marks for both legs

### 4.11 `live/live_runner.py` — LiveRunner

- **`run_forever()`** — infinite loop with `poll_seconds` sleep
- **`run_once()`** — single iteration (same logic as BacktestRunner)
- **State persistence:** saves/loads `SpreadPosition` to/from `state/live_position.json`
- **Trade log:** appends `TradeRecord` to `state/trades.jsonl`
- **Notifications:** calls `notify_fn` with formatted messages (Telegram in run_live.py)

### 4.12 `live/config_live.yaml` — Live Config

```yaml
ema_fast: 288        # ~24 hours at 5m candles
ema_slow: 864        # ~3 days at 5m candles
adx_min: 25.0        # ADX filter active
credit_min: 50
credit_max: 250
spread_width: 600
tp_pct: 1.00
sl_pct: 100.00       # Effectively no stop loss
cooldown_seconds: 7200  # 2 hours
delta_base_url: https://cdn-ind.testnet.deltaex.org  # TESTNET
poll_seconds: 30
```

### 4.13 `backtest.py` — Backtest Entry Point

- Reads `data/BTCUSD_5m.csv`
- Creates `StrategyParams` with backtest-tuned values (EMA 288/864, ADX 25, etc.)
- Instantiates `SimulatedClock`, `HistoricalDataProvider`, `SimulatedExecutor`, `StrategyEngine`
- Runs `BacktestRunner.run()`
- Prints yearly and monthly performance tables, overall summary

### 4.14 `run_live.py` — Live Entry Point

- Loads `.env` for API keys
- Reads `live/config_live.yaml`
- Instantiates `DeltaRestClient`, `WallClock`, `LiveDataProvider`, `LiveExecutor`, `StrategyEngine`
- Creates Telegram notification function
- Runs `LiveRunner.run_forever()` in an infinite loop with Ctrl+C handling

### 4.15 `fetch_data.py` — Data Fetcher

- Fetches up to 105,120 candles (~1 year of 5m data) from Binance API
- Saves to `data/BTCUSD_5m.csv`
- Only runs if CSV doesn't already exist

---

## 5. Strategy Logic Deep Dive

### Signal Detection
1. Compute fast EMA and slow EMA on `close` prices of **closed** candles only
2. If `prev_fast ≤ prev_slow` AND `now_fast > now_slow` → **bull** signal
3. If `prev_fast ≥ prev_slow` AND `now_fast < now_slow` → **bear** signal
4. If `adx_min > 0`: compute ADX, block signal if `adx < adx_min`

### Spread Selection
1. Bull signal → sell **put** credit spread; Bear signal → sell **call** credit spread
2. Try 0dte first (first expiry), then next day (second expiry)
3. For each expiry:
   - Filter chain to correct option type
   - Filter to OTM options (puts below spot for bull, calls above spot for bear)
   - Sort by strike ascending (bear) or descending (bull)
   - For each short strike candidate: calculate long strike = short ± spread_width
   - Calculate net_credit = (short_mark - long_mark) × (1 - slippage_pct)
   - Accept if `credit_min ≤ net_credit ≤ credit_max`

### Exit Logic
- **Profit target:** close when current mark ≤ entry_credit × (1 - tp_pct)
- **Stop loss:** close when current mark ≥ entry_credit × sl_pct
- **Signal cut:** close when opposite crossover appears
- **Expiry:** close remaining positions at backtest end (or at exchange expiry in live)
- **Cooldown:** after closing, wait `cooldown_seconds` before next entry

### PnL Calculation
```
realized_pnl = (entry_credit - exit_credit) × qty - total_commission
```

---

## 6. Data Flow Diagrams

### Backtest Data Flow
```
data/BTCUSD_5m.csv
    ↓
HistoricalDataProvider (SimulatedClock advances candle by candle)
    ↓ get_candles()
StrategyEngine.detect_crossover()
    ↓ Signal
HistoricalDataProvider.get_available_expiries() → get_option_chain()
    ↓ Black-Scholes reconstructed chains
StrategyEngine.select_expiry_and_spread()
    ↓ SpreadCandidate
SimulatedExecutor.open_spread() → FillResult
    ↓
SpreadPosition (stored in BacktestRunner)
    ↓ (next candle: check should_close, check new signal...)
SimulatedExecutor.close_spread() → FillResult
    ↓
TradeRecord (appended to list)
    ↓
BacktestResult → printed by backtest.py
```

### Live Data Flow
```
Delta Exchange REST API
    ↓
LiveDataProvider (WallClock = real time)
    ↓ get_candles()
StrategyEngine.detect_crossover()
    ↓ Signal
LiveDataProvider.get_available_expiries() → get_option_chain()
    ↓ Real option chain from Delta
StrategyEngine.select_expiry_and_spread()
    ↓ SpreadCandidate
LiveExecutor.open_spread() → real market orders + exchange stop
    ↓
SpreadPosition (saved to state/live_position.json)
    ↓ (every 30s: check should_close, check new signal...)
LiveExecutor.close_spread() → real reduce-only orders
    ↓
TradeRecord (appended to state/trades.jsonl)
    ↓
Telegram notification via notify_fn
```

---

## 7. Dependency Graph

### External Dependencies (requirements.txt)
```
requests>=2.31.0        # HTTP client (used by delta_rest.py, fetch_data.py)
python-dotenv>=1.0.0    # .env loading (used by backtest.py, run_live.py)
pandas>=2.0.0           # DataFrames (used everywhere)
pytest>=8.0.0           # Test framework
PyYAML>=6.0             # YAML config loading (used by run_live.py)
```

### Internal Import Graph
```
engine/models.py        ← standalone (no internal imports)
engine/config_schema.py ← standalone
engine/interfaces.py    ← imports from models.py
engine/strategy_engine.py ← imports from config_schema.py, models.py

backtest/historical_data_provider.py ← imports from engine (StrategyParams, Clock, DataProvider)
backtest/simulated_executor.py       ← imports from engine (FillResult, Leg, SpreadPosition, StrategyParams, OrderExecutor)
backtest/backtest_runner.py          ← imports from engine (SpreadCandidate, SpreadPosition, StrategyEngine, StrategyParams, TradeRecord)

live/delta_rest.py       ← standalone (uses requests)
live/live_data_provider.py ← imports from engine.interfaces (Clock, DataProvider)
live/live_executor.py    ← imports from engine (FillResult, Leg, SpreadPosition, StrategyParams, OrderExecutor)
live/live_runner.py      ← imports from engine (all models + StrategyEngine)

backtest.py    ← imports from engine + backtest/
run_live.py    ← imports from engine + live/
fetch_data.py  ← standalone (uses requests, pandas)
```

---

## 8. Test Coverage Summary

| Test File | What It Tests |
|---|---|
| `test_crossover_detection.py` | Bull/bear crossover detection, incomplete bar filtering, ADX filtering |
| `test_spread_selection.py` | 0dte priority, next-day fallback, credit band filtering |
| `test_stop_and_target_logic.py` | Profit target, stop loss, hold (no close), cut-and-reenter on opposite signal, cooldown timing |
| `test_engine_parity.py` | Deterministic engine output (same inputs → same outputs), no I/O imports in engine/ |
| `test_backtest_runner.py` | BacktestRunner produces TradeRecord with correct data_mode="reconstructed" |
| `test_no_lookahead.py` | HistoricalDataProvider only returns bars visible at simulated now, option chain uses correct spot |

---

## 9. Configuration System

### How Configs Are Loaded

1. **Hardcoded defaults** in `StrategyParams` dataclass
2. **YAML overlay** via `params.overlay(yaml_dict)` — only known fields are overwritten
3. **Environment variable overrides** (e.g., `TRADE_QTY`)

### Two Config Profiles

| Parameter | Backtest (`config_backtest.yaml`) | Live (`config_live.yaml`) |
|---|---|---|
| EMA | 9 / 21 | 288 / 864 |
| ADX min | 0 (disabled) | 25.0 |
| Credit band | 150–200 | 50–250 |
| Spread width | 200 | 600 |
| TP% | 50% | 100% (no TP exit) |
| SL% | 200% | 10000% (no SL exit) |
| Cooldown | 900s (15min) | 7200s (2hr) |
| Slippage | 0.25% | 0% |
| Poll | N/A | 30s |

---

## 10. Environment Variables (.env)

| Variable | Used By | Purpose |
|---|---|---|
| `DELTA_API_KEY` | run_live.py | Delta Exchange API key |
| `DELTA_API_SECRET` | run_live.py | Delta Exchange API secret |
| `DELTA_BASE_URL` | run_live.py | Override Delta base URL |
| `TRADE_QTY` | backtest.py, run_live.py | Override trade quantity |
| `TELEGRAM_BOT_TOKEN` | run_live.py | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | run_live.py | Telegram chat ID for notifications |
| `TELEGRAM_ENABLED` | run_live.py | Set "true" to enable Telegram |

---

## 11. Running the System

### Install
```bash
pip install -r requirements.txt
```

### Fetch Historical Data
```bash
python fetch_data.py
```

### Run Backtest
```bash
python backtest.py
```

### Run Live (Testnet)
```bash
python run_live.py
```

### Run Tests
```bash
pytest -q
```

---

## 12. Known Limitations & Design Notes

1. **Backtest uses Black-Scholes reconstruction** — no real option chain data, no bid/ask spreads, no IV skew, no liquidity modeling. All trades tagged `data_mode: reconstructed`.
2. **Live defaults to testnet** — `https://cdn-ind.testnet.deltaex.org`. Production requires setting `DELTA_BASE_URL` env var.
3. **No stop-loss in live config** — `sl_pct: 100.00` effectively disables stop-loss (mark would need to be 100x entry credit). Exchange stop order is placed but at `mark × sl_pct` which is very distant.
4. **No take-profit in live config** — `tp_pct: 1.00` means TP triggers only when mark = 0 (perfect decay).
5. **Cooldown only tracks last close time** — no per-direction cooldown.
6. **Signal cut logic** — opposite crossover closes the current position; re-entry still subject to cooldown.
7. **`.commandcode/taste/taste.md`** — empty file, appears to be a placeholder for code-taste configuration.
