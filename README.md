# Zero-DT Supertrend Options Selling Strategy

Python system for a 3H Supertrend-based options-selling strategy on Delta
Exchange India, with a shared strategy engine driving both a backtester and
a live/paper trading bot.

## Architecture

```
config.py            Single source of truth for all tunables (.env-driven)
engine.py             THE strategy brain. No knowledge of backtest vs live --
                      only talks to a Broker interface.
broker.py             Broker interface (get_option_chain, place_sell_order, ...)

strategy/
  supertrend.py       Incremental Supertrend(16, 1.5) on 3H candles, no look-ahead
  option_selector.py  Strike selection: nearest to Supertrend reference price
                      BUY signal -> sell PUT, SELL signal -> sell CALL
  expiry_selector.py  Today's expiry -> tomorrow's expiry -> no trade (>= $300 premium)
  risk_manager.py     Margin-budget position sizing

exchange/
  delta_client.py     Real Delta Exchange REST client (Broker implementation)
  simulated_broker.py Backtest fills: 'reconstructed' (Black-Scholes) or
                      'realistic' (real historical option data, NOT YET WIRED --
                      see "Critical open item" below)

backtest.py           Replays historical candles through engine.py via SimulatedBroker
live_algo.py          Runs engine.py against live data via DeltaClient (mode=live)
                      or PaperBroker (mode=paper, real data / simulated fills)
run_diagnostic.py     RUN THIS FIRST -- probes your real Delta account to answer
                      every open question below

storage/state_store.py     SQLite persistence (signals, trades, orders, bot_state, errors)
notifications/telegram.py  Telegram messages per your specified templates
```

**Why one engine works for backtest, paper, and live:** `engine.py` never
touches Delta's API or historical files directly -- it only calls methods on
a `Broker`. `backtest.py` hands it a `SimulatedBroker`; `live_algo.py` hands
it `DeltaClient` (live) or `PaperBroker` (paper). Same Supertrend logic, same
strike/expiry selection, same sizing, every time -- this is what prevents
backtest and live logic from silently drifting apart.

## Confirmed decisions (from our conversation)

- Strike selection: nearest available strike to the **Supertrend reference
  price** at signal time (not necessarily current spot/ATM)
- BUY signal -> sell **PUT**; SELL signal -> sell **CALL**
- Position sizing: **margin-budget cap** -- bot computes max lots that fit
  `MARGIN_BUDGET_USD`
- Supertrend ATR period: **16**, multiplier: **1.5**, on **3H** candles
- Persistence: **local SQLite** (no MongoDB)
- Minimum premium: **$300**, checked today's expiry -> tomorrow's -> no trade

## Critical open items -- run `run_diagnostic.py` before trusting any output

These are things I could not verify without live API access, and I did not
want to silently guess and let wrong numbers look correct:

1. **Contract size (`CONTRACT_VALUE_UNDERLYING`)** -- how much BTC one
   option lot represents. Currently a placeholder (`0.001`). This scales
   *every* premium and margin figure in the system. `run_diagnostic.py`
   pulls this from Delta's real `/v2/products` response.

2. **Whether expired option symbols return historical candle data.** This
   determines whether `backtest.py --mode realistic` (real recorded
   premiums) is possible at all, or whether you're limited to
   `--mode reconstructed` (Black-Scholes theoretical premiums, clearly
   labeled `data_mode: reconstructed` in every trade record and flagged with
   a warning in `backtest_summary.json`). If realistic mode is available,
   `HistoricalOptionDataSource` in `exchange/simulated_broker.py` still needs
   the actual data-fetching/caching implementation wired in (currently
   raises `NotImplementedError` as a placeholder).

3. **Margin formula.** `estimate_margin_per_lot()` in both `delta_client.py`
   and `simulated_broker.py` is a flat approximation (15% of per-lot
   notional), NOT Delta's real margin methodology. Needs replacing with
   Delta's actual margin-calculator endpoint/formula before any dollar
   figure (position sizing, backtest P&L) can be trusted.

4. **Finding from a dry run with reconstructed data:** with same-day
   ("Zero-DT") expiries and near-the-money strike selection, theoretical
   premiums come out very low (little time value left), meaning many
   signals may fail the $300 minimum and get skipped or pushed to
   tomorrow's expiry. This might be expected behavior for this strategy, or
   it might mean the assumed volatility (55%, flat, placeholder) is too low
   for crypto 0DTE options. Worth checking against real premiums once
   `run_diagnostic.py` gives you a live number to compare against.

5. **Field names in `delta_client.py`** (`strike_price`, `best_bid`,
   `contract_type`, `settlement_time`, etc.) are based on documented
   endpoint shapes, not a live response I've inspected. `run_diagnostic.py`
   prints a raw sample so you can confirm or correct them.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in DELTA_API_KEY / DELTA_API_SECRET (testnet keys are fine to start),
# TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID

python run_diagnostic.py   # answers items 1, 2, 5 above against your real account
```

Report back what `run_diagnostic.py` prints (and the contents of
`diagnostic_report.json`) and I'll wire the confirmed numbers into
`config.py`, `delta_client.py`, and `simulated_broker.py`.

## Running

```bash
# Backtest (reconstructed/theoretical premiums, works today):
python backtest.py --mode reconstructed --days 180

# Backtest (real historical premiums -- only after item 2 above is resolved
# and HistoricalOptionDataSource is implemented):
python backtest.py --mode realistic --start 2025-01-01 --end 2025-12-31

# Paper trading (live market data, simulated fills, real Telegram alerts):
TRADING_MODE=paper python live_algo.py

# Live trading (real orders -- only after testnet validation):
TRADING_MODE=live python live_algo.py
```

## Azure VM deployment (generic -- tell me if you want this tailored to an
already-provisioned VM/OS)

```bash
# On an Ubuntu 22.04+ VM:
sudo apt update && sudo apt install -y python3-pip python3-venv
git clone <your-repo> zero_dt_strategy && cd zero_dt_strategy
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values

# Run as a systemd service so it survives VM restarts (pairs with the
# SQLite-based reconciliation in live_algo.py's reconcile_startup_state()):
sudo tee /etc/systemd/system/zero-dt-bot.service << 'EOF'
[Unit]
Description=Zero-DT Supertrend Options Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/azureuser/zero_dt_strategy
ExecStart=/home/azureuser/zero_dt_strategy/venv/bin/python live_algo.py
Restart=always
RestartSec=10
EnvironmentFile=/home/azureuser/zero_dt_strategy/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now zero-dt-bot
journalctl -u zero-dt-bot -f   # tail logs
```

## Tested so far (in this sandbox, no network access, no live API keys)

- `strategy/supertrend.py`: verified against synthetic random-walk data --
  correctly detects trend flips, no look-ahead, matches expected Supertrend
  mechanics.
- `engine.py`: full end-to-end test with a mock broker -- confirmed it holds
  through same-direction candles, only reacts on confirmed flips, closes the
  reversed position and opens the correct opposite side (PUT on BUY / CALL
  on SELL) at the nearest strike to the reference price, sized by margin
  budget.
- `backtest.py` (reconstructed mode): full pipeline runs end-to-end on
  synthetic candles, produces `trades.csv` / `backtest_summary.json` with
  correct P&L, drawdown, Sharpe, monthly breakdown.
- **Not yet tested against the real Delta Exchange API** -- no network
  access in this environment and no API keys were provided. `run_diagnostic.py`
  is written against documented endpoint shapes but needs a real run to
  confirm correctness (see open items above).
