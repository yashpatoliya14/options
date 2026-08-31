from dataclasses import dataclass
from datetime import datetime
from typing import Literal


Direction = Literal["bull", "bear"]
OptionType = Literal["put", "call"]
Side = Literal["buy", "sell"]
ExpiryLabel = Literal["0dte", "next_day"]
ExitReason = Literal["profit_target", "stop_loss", "signal_cut", "expired", "cooldown_skip"]


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    direction: Direction
    fast_ema: float
    slow_ema: float
    adx: float | None = None


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
    qty: int
    stop_order_id: str | None = None

    @classmethod
    def from_candidate(
        cls,
        candidate: SpreadCandidate,
        entry_time: datetime,
        entry_credit: float | None = None,
        stop_order_id: str | None = None,
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
            qty=candidate.short_leg.qty,
            stop_order_id=stop_order_id,
        )


@dataclass(frozen=True)
class FillResult:
    ok: bool
    entry_or_exit_credit: float | None
    slippage: float
    commission: float
    legs: list[Leg]
    message: str = ""


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


@dataclass(frozen=True)
class Decision:
    action: Literal["open", "close", "hold", "skip"]
    reason: str
    signal: Signal | None = None
    candidate: SpreadCandidate | None = None
    exit_reason: ExitReason | None = None
