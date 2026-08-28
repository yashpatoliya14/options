# Configuration Guide

> [!NOTE]
> All parameters for the strategy are centralized in `config.py` and are populated via environment variables using the `.env` file. You should never need to edit the Python files to change strategy parameters.

## The `.env` File Structure

Copy `.env.example` to `.env` and configure your settings.

### Supertrend & Strategy Settings
- `TIMEFRAME`: The candle resolution (e.g., `2h`, `3h`).
- `SUPERTREND_ATR_PERIOD`: The lookback period for Average True Range (Default: `16`).
- `SUPERTREND_MULTIPLIER`: The multiplier for the ATR bands (Default: `1.5`).
- `MIN_PREMIUM`: Minimum acceptable premium in USD to take a trade (Default: `250.0`). If the premium is lower, the bot skips the trade or looks for tomorrow's expiry.
- `STOP_LOSS_PERCENT`: If the option premium rises by this percentage above entry, the trade is closed at a loss (Default: `20.0`).
- `TRAILING_BREAKEVEN_PCT`: If the option premium decays by this percentage, the stop loss is moved to the entry price to lock in a risk-free trade (Default: `50.0`).

### Asset & Risk Management
- `UNDERLYING_ASSET`: The asset to trade options on (e.g., `BTC`).
- `UNDERLYING_SYMBOL`: The symbol for fetching candles (e.g., `BTCUSD`).
- `MARGIN_BUDGET_USD`: The maximum amount of margin in USD you want to allocate *per trade*. The bot uses this to calculate position size (Default: `50.0`).
- `CONTRACT_VALUE_UNDERLYING`: The multiplier for the option contract. For Delta BTC options, 1 contract usually equals `0.001` BTC.

### Trading Mode & Credentials
- `TRADING_MODE`: Set to `paper` or `live`.
- `DELTA_API_KEY`: Your Delta Exchange API key (Requires Trading permissions).
- `DELTA_API_SECRET`: Your Delta Exchange API secret.
- `USE_TESTNET`: Set to `true` to point the client to Delta's testnet.

### Telegram Notifications
- `TELEGRAM_ENABLED`: `true` or `false`.
- `TELEGRAM_BOT_TOKEN`: The token from BotFather.
- `TELEGRAM_CHAT_ID`: Your personal chat ID where alerts should be sent.

## Pre-Flight: The Diagnostic Tool

> [!IMPORTANT]
> Before running the bot in `live` or `paper` mode, you MUST run `python run_diagnostic.py`.

Delta Exchange periodically updates contract specifications, fee tiers, and margin requirements. The `run_diagnostic.py` script:
1. Pings the live exchange using your API keys.
2. Extracts the *exact* `contract_value` (e.g. 0.001 BTC per lot).
3. Evaluates your current fee tier.
4. Validates API connectivity.
5. Saves the findings to `diagnostic_report.json`.

You should cross-reference the output of this script with your `.env` variables to ensure the bot sizes positions correctly.
