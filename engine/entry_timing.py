from __future__ import annotations

from datetime import datetime, timedelta

from .config_schema import StrategyParams
from .models import Signal


class SignalGate:
    """
    Shared live/backtest gate: de-duplicate signals and optional delayed entry.

    Immediate entry is the default. Delayed / confirmation modes wait until
    `now` is after the signal candle plus entry_delay_minutes.
    """

    def __init__(self, params: StrategyParams):
        self.params = params
        self._last_key: tuple[datetime, str] | None = None
        self._pending: Signal | None = None

    def reset(self) -> None:
        self._last_key = None
        self._pending = None

    def consume(self, signal: Signal | None, now: datetime, last_close: float | None = None) -> Signal | None:
        if signal is None and self._pending is None:
            return None

        if signal is not None:
            key = (signal.timestamp, signal.direction)
            if key == self._last_key:
                signal = None
            elif self.params.entry_mode == "immediate":
                self._last_key = key
                return signal
            else:
                self._pending = signal

        pending = self._pending
        if pending is None:
            return None

        ready_at = pending.timestamp + timedelta(minutes=max(self.params.entry_delay_minutes, 0))
        if now < ready_at:
            return None

        if self.params.entry_mode == "confirm" and last_close is not None:
            if pending.direction == "bull" and last_close < pending.fast_ema:
                return None
            if pending.direction == "bear" and last_close > pending.fast_ema:
                return None

        self._last_key = (pending.timestamp, pending.direction)
        self._pending = None
        return pending
