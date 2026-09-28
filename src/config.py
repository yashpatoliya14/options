"""Central configuration for the BTCUSD directional premium-harvest backtest.

Strategy (from the Delta Exchange "Poor Man's Covered Put/Call" video):
  1. Read the trend with a SuperTrend filter on the higher-timeframe index.
  2. Trade ONE direction only (never a naked straddle):
       - down-trend -> "covered put":  short the future  + sell short-dated PUTs
       - up-trend   -> "covered call": long the future   + sell short-dated CALLs
  3. Harvest the option premium (theta) day after day while the trend holds.
  4. The directional (future) leg is the hedge: it is exited when SuperTrend
     flips, which caps the loss (the video's alternative to a hard stop / OTM
     hedge). We keep money from *premium decay*, not from the move.

All tunable parameters, paths and Delta Exchange symbols live here so a single
edit changes the whole run.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --- Paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
TAPE_DATASET = CACHE_DIR / "tape"           # partitioned parquet dataset (by expiry)
INDEX_CACHE = CACHE_DIR / "btc_index.parquet"
RESULTS_DIR = ROOT / "results"
STATE_DB = ROOT / "state.db"                # live runner's durable position + trade log

# --- Delta Exchange -------------------------------------------------------
DELTA_BASE = "https://api.india.delta.exchange"
INDEX_SYMBOL = ".DEXBTUSD"                  # BTC spot index used for settlement/marks
LOT_MULTIPLIER = 0.001                      # 1 option contract = 0.001 BTC (verified via API)
SETTLEMENT_HOUR_UTC = 12                    # Delta BTC options settle at 12:00 UTC on expiry date


@dataclass
class StrategyConfig:
    # --- SuperTrend trend filter (higher timeframe) ---
    # 4h filter (matches the reference video). NOTE: 4h reacts faster but whipsaws
    # more than 8h — on the Aug–Sep 2026 index it flips ~9x/40d vs ~4x for 8h.
    # If false signals bite, raise st_multiplier to ~4.0 (that pulls 4h back to ~4
    # flips/40d) rather than dropping back to 8h.
    st_timeframe_hours: int = 4             # resample the 1h index to this candle size
    st_atr_period: int = 15                 # ATR lookback for SuperTrend (matches reference)
    st_multiplier: float = 3.0              # SuperTrend band multiplier

    # --- Short-option (theta) leg ---
    short_target_delta: float = 0.25        # |delta| of the option we sell (OTM, mostly time value)
    short_min_dte_days: float = 0.4         # only sell options expiring at least this far out
    short_max_dte_days: float = 4.0         # ... and at most this far out (prefer the nearest)

    # --- Directional (future) leg ---
    trade_future_leg: bool = True           # hold a 1x index future aligned with the trend
    # (the future is exited automatically when SuperTrend flips)

    # --- Entry timing ---
    entry_hour_utc: int = 8                 # time of day used to snapshot signals / fills (UTC)

    # --- Sizing / costs ---
    lots: int = 1                           # contracts per leg
    lot_multiplier: float = LOT_MULTIPLIER
    fee_rate: float = 0.0003                # 0.03% of notional (taker)
    fee_cap_frac: float = 0.10              # option fee capped at 10% of premium

    # --- Pricing ---
    risk_free_rate: float = 0.0             # r for Black-76 (crypto ~ 0)

    # --- Fill / curve windows ---
    entry_window_hours: int = 24            # VWAP window for entry fills (from before asof)
    min_strikes_for_curve: int = 4          # minimum traded strikes to build a usable IV curve

    def __post_init__(self) -> None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)


CONFIG = StrategyConfig()
