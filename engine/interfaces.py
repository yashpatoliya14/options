from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd

from .models import FillResult, Leg, SpreadPosition


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Return a timezone-aware UTC timestamp."""
        raise NotImplementedError


class DataProvider(ABC):
    @abstractmethod
    def get_candles(self, symbol: str, resolution: str, lookback: int) -> pd.DataFrame:
        """Return closed candles with UTC timestamps and no rows after clock.now()."""
        raise NotImplementedError

    @abstractmethod
    def get_available_expiries(self, underlying: str) -> list[str]:
        """Return ISO expiry dates, soonest first, as of clock.now()."""
        raise NotImplementedError

    @abstractmethod
    def get_option_chain(self, underlying: str, expiry_date: str) -> pd.DataFrame:
        """Return one option-chain snapshot as of clock.now()."""
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Return mark, bid, ask, greeks, and timestamp for a symbol."""
        raise NotImplementedError


class OrderExecutor(ABC):
    @abstractmethod
    def open_spread(self, direction: str, short_leg: Leg, long_leg: Leg) -> FillResult:
        raise NotImplementedError

    @abstractmethod
    def close_spread(self, position: SpreadPosition) -> FillResult:
        raise NotImplementedError

    @abstractmethod
    def mark_to_market(self, position: SpreadPosition) -> float:
        """Return remaining debit to close the net short spread."""
        raise NotImplementedError
