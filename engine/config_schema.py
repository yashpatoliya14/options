from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class StrategyParams:
    underlying: str = "BTC"
    candle_symbol: str = "BTCUSD"
    resolution: str = "1h"

    # --- Supertrend primary signal ---
    supertrend_atr_period: int = 15
    supertrend_multiplier: float = 1.5
    supertrend_timeframe: str = "1h"
    signal_requires_close: bool = True

    # --- Trend filter (EMA-based, reduces whipsaw) ---
    trend_filter_enabled: bool = True
    trend_filter_period: int = 50       # EMA period: BUY only above, SELL only below
    min_trend_bars: int = 2             # Supertrend must hold for N bars before signal

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
    min_credit_risk_ratio: float = 0.90  # Credit / max loss; 0.90 is near 1:1.
    target_credit_risk_ratio: float = 1.00  # Prefer screenshot-like 1:1 payoff.
    spread_type: str = "directional"   # "directional" or "credit"
    bull_structure: str = "put_credit"     # put_credit (Bull Put)
    bear_structure: str = "call_credit"    # call_credit (Bear Call) | put_credit (bullish put credit) | put_debit (Bear Put)
    strike_offset_pct: float = 0.015   # How far OTM to place short strike (1.5%)
    expiry_selection: str = "cutoff_hour"  # cutoff_hour | target_dte | nearest_valid_after_signal
    strike_selection: str = "otm_pct"  # otm_pct | delta | vol_adjusted | atm_or_nearest_otm
    target_short_delta: float = 0.25
    target_long_delta: float = 0.45    # Bear Put Spread long-put |delta| target
    target_dte: float = 1.0
    min_dte: float = 0.0
    max_dte: float = 14.0
    vol_adjusted_k: float = 1.0

    # --- Exit logic ---
    tp_pct: float = 0.50
    sl_pct: float = 2.00
    stop_loss_enabled: bool = False
    stop_loss_pct: float = 1.00
    take_profit_pct: float = 0.50
    exit_on_opposite_signal: bool = False
    reversal_profit_capture_pct: float = 0.50
    cooldown_seconds: int = 900
    expiry_cutoff_hour: int = 9        # UTC hour: >= this → next-day expiry
    early_exit_minutes: int = 60       # Time-based exit for pre-cutoff signals
    max_hold_hours: float = 0.0        # >0 enables global time-based exit

    # --- Sizing / costs ---
    qty: int = 1
    slippage_pct: float = 0.0
    commission_per_leg: float = 0.0    # flat per-contract fee per leg
    settlement_fee_pct: float = 0.0    # taker fee as fraction of leg notional (Delta: 0.00125)
    contract_size: float = 1.0         # qty of underlying per contract (BTC: 0.001)
    bid_ask_spread_pct: float = 0.01
    liquidity_limit: int = 3
    capital: float = 10000.0
    max_position_value_pct: float = 0.10

    # --- Risk-based sizing ---
    sizing_mode: str = "fixed_qty"     # fixed_qty | fixed_risk
    risk_pct: float = 0.01             # fraction of equity risked per trade
    max_risk_pct: float = 0.03         # hard cap on per-trade risk fraction
    dd_risk_reduction: bool = False    # scale risk down inside drawdown
    dd_risk_threshold: float = 0.10    # drawdown fraction where floor applies
    dd_risk_floor_pct: float = 0.5     # risk multiplier floor at/under threshold

    # --- Black-Scholes reconstruction ---
    option_data_mode: str = "reconstructed"
    assumed_iv: float = 0.55
    risk_free_rate: float = 0.0
    iv_mode: str = "constant"  # constant | realized_scaled
    rv_window: int = 24

    # --- Execution realism ---
    use_exchange_stop: bool = False     # Set true only after Delta confirms spread-level/OCO stop
    fill_model: str = "bid_ask"  # mark | bid_ask
    execution_delay_seconds: int = 0
    drop_incomplete_bars: bool = True

    # --- Liquidity ---
    liquidity_filter_enabled: bool = True
    max_bid_ask_pct: float = 0.15
    min_volume: float = 0.0
    min_open_interest: float = 0.0
    max_slippage_pct: float = 0.05

    # --- Optional independent filters (off unless enabled) ---
    adx_filter_enabled: bool = False
    rsi_filter_enabled: bool = False
    iv_rv_filter_enabled: bool = False
    iv_rv_min_ratio: float = 1.0
    session_filter_enabled: bool = False
    allowed_utc_hours: str = ""  # comma-separated UTC hours, empty = all
    atr_pct_filter_enabled: bool = False  # block entries when ATR% > max
    max_atr_pct: float = 0.03
    min_atr_pct: float = 0.0

    # --- Entry timing ---
    entry_mode: str = "immediate"  # immediate | delay | confirm | pullback
    entry_delay_minutes: int = 0

    # --- Greek-aware exit ---
    greek_exit_enabled: bool = False
    max_abs_net_delta: float = 0.80
    min_dte_exit: float = 0.0

    # --- Spread scoring (disabled = first valid candidate) ---
    spread_scoring_enabled: bool = False
    score_direction_weight: float = 1.0
    score_liquidity_weight: float = 1.0
    score_delta_weight: float = 1.0
    score_iv_weight: float = 0.5
    score_dte_weight: float = 0.5
    score_spread_penalty: float = 1.0
    score_gamma_penalty: float = 0.0

    def __post_init__(self) -> None:
        if self.supertrend_atr_period <= 0:
            raise ValueError("supertrend_atr_period must be positive")
        if self.supertrend_multiplier <= 0:
            raise ValueError("supertrend_multiplier must be positive")
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
        if self.min_credit_risk_ratio < 0.0:
            raise ValueError("min_credit_risk_ratio must be non-negative")
        if self.target_credit_risk_ratio < 0.0:
            raise ValueError("target_credit_risk_ratio must be non-negative")
        if self.tp_pct < 0:
            raise ValueError("tp_pct must be non-negative")
        if self.sl_pct <= 0:
            raise ValueError("sl_pct must be positive")
        if self.stop_loss_pct <= 0:
            raise ValueError("stop_loss_pct must be positive")
        if self.take_profit_pct < 0:
            raise ValueError("take_profit_pct must be non-negative")
        if not 0.0 <= self.reversal_profit_capture_pct <= 1.0:
            raise ValueError("reversal_profit_capture_pct must be in [0, 1]")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if self.slippage_pct < 0:
            raise ValueError("slippage_pct must be non-negative")
        if self.commission_per_leg < 0:
            raise ValueError("commission_per_leg must be non-negative")
        if self.settlement_fee_pct < 0 or self.settlement_fee_pct > 0.05:
            raise ValueError("settlement_fee_pct must be in [0, 0.05]")
        if self.contract_size <= 0:
            raise ValueError("contract_size must be positive")
        if self.assumed_iv <= 0:
            raise ValueError("assumed_iv must be positive")
        if self.rsi_period <= 0:
            raise ValueError("rsi_period must be positive")
        if self.adx_period <= 0:
            raise ValueError("adx_period must be positive")
        if self.capital <= 0:
            raise ValueError("capital must be positive")
        if self.risk_pct <= 0:
            raise ValueError("risk_pct must be positive")
        if self.max_risk_pct < self.risk_pct:
            raise ValueError("max_risk_pct must be >= risk_pct")
        if self.max_bid_ask_pct < 0:
            raise ValueError("max_bid_ask_pct must be non-negative")
        if self.fill_model not in {"mark", "bid_ask"}:
            raise ValueError("fill_model must be mark or bid_ask")
        if self.spread_type not in {"directional", "credit"}:
            raise ValueError("unsupported spread_type")
        if self.bull_structure not in {"put_credit"}:
            raise ValueError("unsupported bull_structure")
        if self.bear_structure not in {"put_credit", "call_credit", "put_debit"}:
            raise ValueError("unsupported bear_structure")
        if self.entry_mode not in {"immediate", "delay", "confirm", "pullback"}:
            raise ValueError("unsupported entry_mode")
        if self.tp_pct != 0.50 and self.take_profit_pct == 0.50:
            object.__setattr__(self, "take_profit_pct", self.tp_pct)
        if self.sl_pct != 2.00 and self.stop_loss_pct == 1.00:
            object.__setattr__(self, "stop_loss_pct", self.sl_pct)
        if self.strike_selection not in {"otm_pct", "delta", "vol_adjusted", "atm", "atm_or_nearest_otm"}:
            raise ValueError("unsupported strike_selection")
        if self.expiry_selection not in {"cutoff_hour", "target_dte", "nearest_valid_after_signal"}:
            raise ValueError("unsupported expiry_selection")

    def required_lookback(self) -> int:
        trend = self.trend_filter_period + 10 if self.trend_filter_enabled else 0
        return max(
            self.supertrend_atr_period + 20,
            self.rsi_period + 5,
            self.adx_period * 2 + 5,
            trend,
            self.rv_window + 5,
            self.ema_slow + self.adx_period + 5,
            100,
        )

    def overlay(self, values: dict[str, Any]) -> "StrategyParams":
        known = {field: value for field, value in values.items() if hasattr(self, field)}
        return replace(self, **known)
