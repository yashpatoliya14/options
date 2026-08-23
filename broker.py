"""
Broker abstraction.

engine.py NEVER talks to Delta Exchange's API or a CSV file directly. It only
calls methods on a Broker instance. This is what guarantees backtest and live
trading run identical strategy logic:

    backtest.py   -> SimulatedBroker (fills against historical option data)
    live_algo.py  -> RealBroker (Delta Exchange live API)     [TRADING_MODE=live]
                  -> PaperBroker (Delta Exchange live data,   [TRADING_MODE=paper]
                                  simulated fills, no real orders)

All three implement the same interface below.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class OptionQuote:
    symbol: str
    option_type: str      # 'put' or 'call'
    strike: float
    expiry: str            # 'YYYY-MM-DD'
    premium: float         # best available price to SELL at (bid, realistically)
    underlying_price: float
    timestamp: int


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str]
    filled_premium: Optional[float]
    quantity: int
    message: str = ""


@dataclass
class Position:
    symbol: str
    option_type: str
    strike: float
    expiry: str
    side: str               # 'sell' (this strategy only ever sells options)
    quantity: int
    entry_premium: float
    entry_timestamp: int
    strategy_direction: str  # 'BUY' or 'SELL' -- which Supertrend state opened this


class Broker(ABC):
    @abstractmethod
    def get_option_chain(self, underlying: str, expiry: str, timestamp: Optional[int] = None) -> List[OptionQuote]:
        """
        Return all available option quotes for `underlying` at `expiry`.
        `timestamp` is only meaningful for SimulatedBroker (point-in-time
        historical lookup); RealBroker/PaperBroker ignore it and return the
        current live chain.
        """
        raise NotImplementedError

    @abstractmethod
    def get_available_expiries(self, underlying: str, as_of_timestamp: Optional[int] = None) -> List[str]:
        """Return available expiry dates, nearest first (today's expiry, then tomorrow's, ...)."""
        raise NotImplementedError

    @abstractmethod
    def estimate_margin_per_lot(self, quote: OptionQuote) -> float:
        """Estimate the margin (USD) required to sell one lot of this contract."""
        raise NotImplementedError

    @abstractmethod
    def place_sell_order(self, quote: OptionQuote, quantity: int) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def close_position(self, position: Position) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def get_open_positions(self) -> List[Position]:
        """Used by live_algo.py on startup to reconcile against exchange state."""
        raise NotImplementedError
