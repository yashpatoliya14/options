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
    timeframe: str = os.getenv("TIMEFRAME", "2h")
    supertrend_atr_period: int = _int_env("SUPERTREND_ATR_PERIOD", 16)
    supertrend_multiplier: float = _float_env("SUPERTREND_MULTIPLIER", 1.5)

    # --- Option selection ---
    strike_selection_method: str = os.getenv("STRIKE_SELECTION_METHOD", "supertrend_otm")
    # Minimum premium of at least $250 per 1 BTC contract
    min_premium_usd: float = _float_env("MIN_PREMIUM", 250.0)
    
    # --- Advanced Strategy Features ---
    htf_timeframe: str = os.getenv("HTF_TIMEFRAME", "1d")
    use_underlying_sl: bool = _bool_env("USE_UNDERLYING_SL", True)
    trailing_breakeven_pct: float = _float_env("TRAILING_BREAKEVEN_PCT", 50.0)
    
    # --- Stop Loss (Premium-based fallback) ---
    stop_loss_percent: float = _float_env("STOP_LOSS_PERCENT", 20.0)

    # --- Underlying / instrument ---
    underlying_asset: str = os.getenv("UNDERLYING_ASSET", "BTC")
    underlying_symbol: str = os.getenv("UNDERLYING_SYMBOL", "BTCUSD")

    # VERIFIED from diagnostic report -- how much underlying (in BTC) one option contract
    # ("1 lot") actually represents on Delta Exchange. This directly scales
    # every premium and margin number in the system. Confirmed from the real 
    # /v2/products response via run_diagnostic.py.
    # Diagnostic report shows "contract_value": "0.001" in sample product.
    contract_value_underlying: float = _float_env("CONTRACT_VALUE_UNDERLYING", 0.001)

    # --- Risk / sizing ---
    # Balance available for margin (set by backtest from capital * leverage)
    margin_budget_usd: float = _float_env("MARGIN_BUDGET_USD", 50.0)
    # Fixed lot size per trade -- every trade uses exactly this many lots
    fixed_lot_size: int = _int_env("FIXED_LOT_SIZE", 5)
    max_daily_loss_usd: float = _float_env("MAX_DAILY_LOSS", 0.0)  # 0 = disabled
    max_open_positions: int = _int_env("MAX_OPEN_POSITIONS", 1)
    slippage_pct: float = _float_env("SLIPPAGE_PCT", 0.5)  # % of premium, applied against the trader
    fee_pct: float = _float_env("FEE_PCT", 0.01)  # % of notional, based on diagnostic report showing 0.01% (0.0001) fee rate

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
