from dataclasses import dataclass
from datetime import datetime
from typing import Literal


Direction = Literal["bull", "bear"]
OptionType = Literal["put", "call"]
Side = Literal["buy", "sell"]
ExpiryLabel = Literal["0dte", "next_day"]
ExpiryType = Literal["same_day", "next_day"]
SignalType = Literal["buy", "sell"]
ExitReason = Literal["profit_target", "stop_loss", "signal_cut", "expired", "cooldown_skip", "time_exit"]


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    direction: Direction
    fast_ema: float
    slow_ema: float
    adx: float | None = None
    rsi: float | None = None
    ema_trend: str | None = None       # "bullish_structure" / "bearish_structure"
    signal_type: str | None = None     # "buy" / "sell"
    atr_pct: float | None = None       # ATR / close at signal candle
    rv: float | None = None            # annualized realized volatility at signal


@dataclass(frozen=True)
class Leg:
    symbol: str
    option_type: OptionType
    strike: float
    expiry: str
    side: Side
    qty: int
    product_id: int | None = None


@dataclass(frozen=True)
class SpreadCandidate:
    direction: Direction
    expiry: str
    expiry_label: ExpiryLabel
    short_leg: Leg
    long_leg: Leg
    net_credit: float
    width: float
    _score: float = 0.0   # ranking score when spread_scoring_enabled


@dataclass(frozen=True)
class SpreadPosition:
    direction: Direction
    expiry: str
    expiry_label: ExpiryLabel
    short_leg: Leg
    long_leg: Leg
    net_credit: float
    width: float
    entry_time: datetime
    entry_credit: float
    entry_spot: float
    qty: int
    stop_order_id: str | None = None
    signal_type: str | None = None        # "buy" / "sell"
    expiry_type: str | None = None        # "same_day" / "next_day"

    @classmethod
    def from_candidate(
        cls,
        candidate: SpreadCandidate,
        entry_time: datetime,
        entry_spot: float = 0.0,
        entry_credit: float | None = None,
        stop_order_id: str | None = None,
        signal_type: str | None = None,
        expiry_type: str | None = None,
    ) -> "SpreadPosition":
        return cls(
            direction=candidate.direction,
            expiry=candidate.expiry,
            expiry_label=candidate.expiry_label,
            short_leg=candidate.short_leg,
            long_leg=candidate.long_leg,
            net_credit=candidate.net_credit,
            width=candidate.width,
            entry_time=entry_time,
            entry_credit=candidate.net_credit if entry_credit is None else entry_credit,
            entry_spot=entry_spot,
            qty=candidate.short_leg.qty,
            stop_order_id=stop_order_id,
            signal_type=signal_type,
            expiry_type=expiry_type,
        )


@dataclass(frozen=True)
class FillResult:
    ok: bool
    entry_or_exit_credit: float | None
    slippage: float
    commission: float
    legs: list[Leg]
    message: str = ""
    submitted_at: datetime | None = None
    acked_at: datetime | None = None
    filled_at: datetime | None = None
    short_fill: float | None = None
    long_fill: float | None = None
    short_bid: float | None = None
    short_ask: float | None = None
    long_bid: float | None = None
    long_ask: float | None = None
    short_mid: float | None = None
    long_mid: float | None = None
    stop_order_id: str | None = None


@dataclass(frozen=True)
class TradeRecord:
    entry_time: datetime
    exit_time: datetime
    direction: Direction
    expiry: str
    expiry_label: ExpiryLabel
    short_strike: float
    long_strike: float
    credit_received: float
    exit_reason: ExitReason
    realized_pnl: float
    slippage: float
    commission: float
    data_mode: str
    underlying: str = "BTC"
    signal_type: str = "buy"
    expiry_type: str = "same_day"
    trade_duration_minutes: float = 0.0
    underlying_price: float = 0.0
    short_premium: float = 0.0
    long_premium: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0
    fees: float = 0.0
    entry_slippage: float = 0.0
    signal_time: datetime | None = None
    detection_time: datetime | None = None
    option_chain_time: datetime | None = None
    option_select_time: datetime | None = None
    order_submit_time: datetime | None = None
    order_ack_time: datetime | None = None
    fill_time: datetime | None = None
    signal_to_fill_seconds: float | None = None
    short_entry_bid: float | None = None
    short_entry_ask: float | None = None
    long_entry_bid: float | None = None
    long_entry_ask: float | None = None
    short_fill_price: float | None = None
    long_fill_price: float | None = None
    net_spread_entry: float | None = None
    theoretical_mid_entry: float | None = None
    iv: float | None = None
    realized_vol: float | None = None
    adx: float | None = None
    rsi: float | None = None
    atr_pct: float | None = None
    utc_hour: int | None = None
    short_delta: float | None = None
    long_delta: float | None = None
    net_delta: float | None = None
    net_theta: float | None = None
    net_vega: float | None = None
    net_gamma: float | None = None
    dte_at_entry: float | None = None
    pnl_delta: float | None = None
    pnl_theta: float | None = None
    pnl_vega: float | None = None
    pnl_gamma: float | None = None
    pnl_execution: float | None = None
    backtest_theoretical_entry: float | None = None
    live_actual_entry: float | None = None
    entry_decay: float | None = None


@dataclass(frozen=True)
class Decision:
    action: Literal["open", "close", "hold", "skip"]
    reason: str
    signal: Signal | None = None
    candidate: SpreadCandidate | None = None
    exit_reason: ExitReason | None = None
