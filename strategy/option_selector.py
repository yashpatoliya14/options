"""
Option strike selection.

Confirmed rule: select the strike whose price is NEAREST to the Supertrend
reference price at the moment of the signal (not necessarily ATM relative to
current spot -- these can differ if spot has moved since the signal candle
closed).

Confirmed direction mapping:
  Supertrend BUY  -> sell a PUT
  Supertrend SELL -> sell a CALL
"""
from typing import List, Optional

from broker import OptionQuote


def option_type_for_signal(signal: str) -> str:
    """signal is 'BUY' or 'SELL' (Supertrend.Trend.value)."""
    if signal == "BUY":
        return "put"
    elif signal == "SELL":
        return "call"
    raise ValueError(f"Unknown signal: {signal}")


def select_strike(
    quotes: List[OptionQuote],
    reference_price: float,
    option_type: str,
) -> Optional[OptionQuote]:
    """
    From a list of quotes (already filtered to one expiry), pick the one of
    `option_type` whose strike is closest to `reference_price`.
    Returns None if no quote of that type exists.
    """
    candidates = [q for q in quotes if q.option_type == option_type]
    if not candidates:
        return None
    return min(candidates, key=lambda q: abs(q.strike - reference_price))
