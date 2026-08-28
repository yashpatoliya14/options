# Execution Modes

> [!NOTE]
> The bot can run in three entirely separate modes using the same core logic. This is controlled by the runner script (`backtest.py` vs `live_algo.py`) and the `TRADING_MODE` environment variable.

## 1. Backtest Mode (`backtest.py`)

Used to simulate historical performance over months or years.

- **How it runs:** `python backtest.py --mode reconstructed --days 180`
- **Broker Used:** `SimulatedBroker`
- **Data Modes:**
  - `reconstructed`: Because historical bid/ask options data is hard to get, this mode uses the Black-Scholes formula to estimate what the premium *would have been* based on the underlying asset's historical price. **This is theoretical and directional.**
  - `realistic`: Requires a database of actual recorded option chains from Delta Exchange. 
- **Output:** Generates comprehensive P&L tables, maximum drawdowns, equity curves, and saves them to `backtest_results/`.

## 2. Paper Trading Mode (`live_algo.py`)

Used to test the bot in real-time market conditions without risking real money.

- **How it runs:** Set `TRADING_MODE=paper` in `.env`, then run `python live_algo.py`.
- **Broker Used:** `PaperBroker` (A wrapper around `DeltaClient`)
- **How it works:** 
  - It fetches **real** live candles and **real** live option chains from the Delta API.
  - When the engine decides to trade, `PaperBroker` intercepts the order. Instead of sending it to the exchange, it logs a "simulated fill" based on the real-time quote (minus simulated slippage).
  - It saves the position to the local SQLite database.
  - **Telegram alerts work exactly like live mode.**

> [!TIP]
> Always run in Paper mode for a few days before switching to Live mode to ensure your Telegram notifications, server stability, and configs are correct.

## 3. Live Trading Mode (`live_algo.py`)

Used to place real orders with real money on Delta Exchange.

- **How it runs:** Set `TRADING_MODE=live` in `.env`, ensure `DELTA_API_KEY` and `DELTA_API_SECRET` are correct, then run `python live_algo.py`.
- **Broker Used:** `DeltaClient`
- **How it works:**
  - Connects securely to Delta Exchange using HMAC-SHA256 authenticated REST requests.
  - Places real Market Sell orders to open positions and Market Buy orders to close them.

### Startup Reconciliation (Crucial Safety Feature)

When `live_algo.py` starts in `live` mode, it performs a strict reconciliation process:
1. It queries Delta Exchange for actual open positions.
2. It queries the local SQLite database for what it *thinks* is open.
3. If Delta shows an open position but the DB doesn't, it adopts the exchange's state to prevent opening a duplicate position.
4. If the DB shows a position but Delta doesn't, it assumes it was closed manually and clears the local DB state.

> [!CAUTION]
> If you manually close a bot's position via the Delta Exchange website, the bot will gracefully realize it's gone upon restart or next API check, but it is best to let the bot manage its own lifecycle.
