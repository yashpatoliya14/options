"""
Supertrend indicator.

Design constraints (per spec):
  - Calculated on 3H OHLC candles.
  - ATR period = 16, multiplier = 1.5 (confirmed; see config.py).
  - A signal is only considered "confirmed" when a 3H candle CLOSES and the
    Supertrend state changes relative to the previous closed candle.
  - No look-ahead: at candle i, only data from candles [0..i] is used. The
    caller must never call `update()` with a candle that hasn't closed yet.

This module is pure computation -- it has no knowledge of exchanges, options,
or trading. It is shared identically between backtest.py and live_algo.py via
engine.py, which is what guarantees backtest/live parity.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence


class Trend(Enum):
    BULLISH = "BUY"
    BEARISH = "SELL"


@dataclass
class Candle:
    timestamp: int  # unix seconds, candle CLOSE time
    open: float
    high: float
    low: float
    close: float


@dataclass
class SupertrendPoint:
    timestamp: int
    value: float          # the Supertrend line value at this candle
    trend: Trend           # state at this candle
    reference_price: float  # underlying close at the moment of this candle
    signal_change: bool     # True only if trend flipped vs previous candle


class SupertrendCalculator:
    """
    Stateful, incremental Supertrend calculator so the exact same object can
    be used to (a) replay history bar-by-bar in backtest.py or (b) consume a
    live stream of closed candles in live_algo.py -- identical code path.
    """

    def __init__(self, atr_period: int = 16, multiplier: float = 1.5):
        if atr_period < 1:
            raise ValueError("atr_period must be >= 1")
        self.atr_period = atr_period
        self.multiplier = multiplier

        self._closes: List[float] = []
        self._highs: List[float] = []
        self._lows: List[float] = []
        self._true_ranges: List[float] = []
        self._atr: Optional[float] = None

        self._final_upper: Optional[float] = None
        self._final_lower: Optional[float] = None
        self._supertrend: Optional[float] = None
        self._trend: Optional[Trend] = None

        self.history: List[SupertrendPoint] = []

    def _true_range(self, high: float, low: float, prev_close: Optional[float]) -> float:
        if prev_close is None:
            return high - low
        return max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

    def update(self, candle: Candle) -> Optional[SupertrendPoint]:
        """
        Feed one CLOSED candle. Returns a SupertrendPoint once enough data
        exists to compute a value (i.e. after atr_period candles), else None.
        Never call this with an unclosed/forming candle -- that is how
        look-ahead bias creeps in.
        """
        prev_close = self._closes[-1] if self._closes else None
        tr = self._true_range(candle.high, candle.low, prev_close)

        self._closes.append(candle.close)
        self._highs.append(candle.high)
        self._lows.append(candle.low)
        self._true_ranges.append(tr)

        if len(self._true_ranges) < self.atr_period:
            return None  # not enough data yet

        if self._atr is None:
            # seed ATR with a simple average of the first atr_period true ranges
            self._atr = sum(self._true_ranges[-self.atr_period:]) / self.atr_period
        else:
            # Wilder's smoothing
            self._atr = (self._atr * (self.atr_period - 1) + tr) / self.atr_period

        hl2 = (candle.high + candle.low) / 2
        basic_upper = hl2 + self.multiplier * self._atr
        basic_lower = hl2 - self.multiplier * self._atr

        if self._final_upper is None or self._final_lower is None:
            final_upper = basic_upper
            final_lower = basic_lower
        else:
            final_upper = (
                basic_upper
                if (basic_upper < self._final_upper or prev_close > self._final_upper)
                else self._final_upper
            )
            final_lower = (
                basic_lower
                if (basic_lower > self._final_lower or prev_close < self._final_lower)
                else self._final_lower
            )

        if self._supertrend is None:
            # initialize trend by comparing close to the basic bands
            trend = Trend.BULLISH if candle.close > final_upper else Trend.BEARISH
            supertrend_value = final_lower if trend == Trend.BULLISH else final_upper
        else:
            prev_trend = self._trend
            if prev_trend == Trend.BULLISH:
                if candle.close < final_lower:
                    trend = Trend.BEARISH
                    supertrend_value = final_upper
                else:
                    trend = Trend.BULLISH
                    supertrend_value = final_lower
            else:  # prev_trend == BEARISH
                if candle.close > final_upper:
                    trend = Trend.BULLISH
                    supertrend_value = final_lower
                else:
                    trend = Trend.BEARISH
                    supertrend_value = final_upper

        signal_change = self._trend is not None and trend != self._trend

        self._final_upper = final_upper
        self._final_lower = final_lower
        self._supertrend = supertrend_value
        self._trend = trend

        point = SupertrendPoint(
            timestamp=candle.timestamp,
            value=supertrend_value,
            trend=trend,
            reference_price=candle.close,
            signal_change=signal_change,
        )
        self.history.append(point)
        return point

    def bulk_load(self, candles: Sequence[Candle]) -> List[SupertrendPoint]:
        """Convenience for backtest.py: feed a full historical series in order."""
        out = []
        for c in candles:
            p = self.update(c)
            if p is not None:
                out.append(p)
        return out
