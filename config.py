"""
Central configuration for the Zero-DT Supertrend options-selling strategy.

Every tunable lives here and is sourced from environment variables (.env).
Nothing below should be hard-coded elsewhere in the codebase -- strategy/,
backtest.py, and live_algo.py should all import Config from here.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _float_env(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _int_env(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class StrategyConfig:
    # --- Supertrend ---
    timeframe: str = os.getenv("TIMEFRAME", "3h")
    supertrend_atr_period: int = _int_env("SUPERTREND_ATR_PERIOD", 16)
    supertrend_multiplier: float = _float_env("SUPERTREND_MULTIPLIER", 1.5)

    # --- Underlying / instrument ---
    underlying_asset: str = os.getenv("UNDERLYING_ASSET", "BTC")
    underlying_symbol: str = os.getenv("UNDERLYING_SYMBOL", "BTCUSD")

    # UNVERIFIED PLACEHOLDER -- how much underlying (in BTC) one option contract
    # ("1 lot") actually represents on Delta Exchange. This directly scales
    # every premium and margin number in the system. Must be confirmed from
    # the real /v2/products response (contract_value / lot size field) via
    # run_diagnostic.py before trusting ANY dollar figure this system produces.
    # 0.001 is a placeholder guess based on typical small-lot crypto options
    # sizing, NOT a confirmed Delta Exchange spec.
    contract_value_underlying: float = _float_env("CONTRACT_VALUE_UNDERLYING", 0.001)

    # --- Option selection ---
    # 'nearest_to_reference' = nearest available strike to the Supertrend
    # reference price at the moment of the signal (confirmed choice).
    strike_selection_method: str = os.getenv("STRIKE_SELECTION_METHOD", "nearest_to_reference")
    min_premium_usd: float = _float_env("MIN_PREMIUM", 300.0)

    # --- Risk / sizing ---
    # Confirmed: margin-budget sizing. The engine computes the max number of
    # lots that fit within this margin budget for the selected contract,
    # using Delta's live/estimated margin requirement per lot.
    margin_budget_usd: float = _float_env("MARGIN_BUDGET_USD", 50.0)
    max_position_size: int = _int_env("MAX_POSITION_SIZE", 0)  # 0 = unbounded by count, margin budget governs
    max_margin_usage_usd: float = _float_env("MAX_MARGIN_USAGE", 0.0)  # 0 = use margin_budget_usd only
    max_daily_loss_usd: float = _float_env("MAX_DAILY_LOSS", 0.0)  # 0 = disabled
    max_open_positions: int = _int_env("MAX_OPEN_POSITIONS", 1)
    slippage_pct: float = _float_env("SLIPPAGE_PCT", 0.5)  # % of premium, applied against the trader
    fee_pct: float = _float_env("FEE_PCT", 0.03)  # % of notional, Delta-style taker/maker approx -- verify against live fee schedule

    # --- Trading mode ---
    trading_mode: str = os.getenv("TRADING_MODE", "paper")  # backtest | paper | live

    # --- Delta Exchange API ---
    delta_api_key: str = os.getenv("DELTA_API_KEY", "")
    delta_api_secret: str = os.getenv("DELTA_API_SECRET", "")
    delta_base_url: str = os.getenv(
        "DELTA_BASE_URL",
        "https://api.india.delta.exchange" if _bool_env("USE_TESTNET", False) is False
        else "https://cdn-ind.testnet.deltaex.org",
    )

    # --- Telegram ---
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_enabled: bool = _bool_env("TELEGRAM_ENABLED", True)

    # --- Persistence ---
    # Confirmed: local file-based persistence (SQLite), no MongoDB.
    sqlite_path: str = os.getenv("SQLITE_PATH", "state/bot_state.db")

    # --- Backtest ---
    backtest_start_date: str = os.getenv("BACKTEST_START_DATE", "")  # '' = as far back as available
    backtest_end_date: str = os.getenv("BACKTEST_END_DATE", "")      # '' = now
    backtest_data_mode: str = os.getenv("BACKTEST_DATA_MODE", "auto")  # auto | realistic | reconstructed


CONFIG = StrategyConfig()
