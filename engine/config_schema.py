from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class StrategyParams:
    underlying: str = "BTC"
    candle_symbol: str = "BTCUSD"
    resolution: str = "5m"

    # --- Legacy EMA crossover params (kept for backward compat) ---
    ema_fast: int = 9
    ema_slow: int = 21

    # --- ADX trend filter ---
    adx_period: int = 14
    adx_min: float = 0.0           # Legacy: minimum ADX for crossover signal
    adx_trend_threshold: float = 20.0  # Directional: ADX must exceed this

    # --- EMA trend structure ---
    ema_trend_fast: int = 9
    ema_trend_slow: int = 21

    # --- RSI momentum ---
    rsi_period: int = 14
    rsi_oversold: float = 35.0     # Buy confirmation zone
    rsi_overbought: float = 65.0   # Sell confirmation zone

    # --- Spread / options ---
    credit_min: float = 150.0
    credit_max: float = 200.0
    spread_width: float = 200.0
    spread_type: str = "directional"   # "directional" or "credit"
    strike_offset_pct: float = 0.015   # How far OTM to place short strike (1.5%)

    # --- Exit logic ---
    tp_pct: float = 0.50
    sl_pct: float = 2.00
    cooldown_seconds: int = 900
    expiry_cutoff_hour: int = 9        # UTC hour: >= this → next-day expiry
    early_exit_minutes: int = 60       # Time-based exit for pre-cutoff signals

    # --- Sizing / costs ---
    qty: int = 1
    slippage_pct: float = 0.0
    commission_per_leg: float = 0.0

    # --- Black-Scholes reconstruction ---
    option_data_mode: str = "reconstructed"
    assumed_iv: float = 0.55
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.ema_fast <= 0 or self.ema_slow <= 0:
            raise ValueError("EMA periods must be positive")
        if self.ema_trend_fast <= 0 or self.ema_trend_slow <= 0:
            raise ValueError("EMA trend periods must be positive")
        if self.ema_trend_fast >= self.ema_trend_slow:
            raise ValueError("ema_trend_fast must be less than ema_trend_slow")
        if self.credit_min < 0 or self.credit_max < self.credit_min:
            raise ValueError("credit band must be non-negative and ordered")
        if self.spread_width <= 0:
            raise ValueError("spread_width must be positive")
        if self.tp_pct < 0:
            raise ValueError("tp_pct must be non-negative")
        if self.sl_pct <= 0:
            raise ValueError("sl_pct must be positive")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.slippage_pct < 0:
            raise ValueError("slippage_pct must be non-negative")
        if self.commission_per_leg < 0:
            raise ValueError("commission_per_leg must be non-negative")
        if self.assumed_iv <= 0:
            raise ValueError("assumed_iv must be positive")
        if self.rsi_period <= 0:
            raise ValueError("rsi_period must be positive")
        if self.adx_period <= 0:
            raise ValueError("adx_period must be positive")

    def overlay(self, values: dict[str, Any]) -> "StrategyParams":
        known = {field: value for field, value in values.items() if hasattr(self, field)}
        return replace(self, **known)
