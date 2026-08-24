"""
Option strike selection.

Confirmed direction mapping:
  Supertrend BUY  -> sell a PUT
  Supertrend SELL -> sell a CALL

Strike rule:
  - The selected strike must be strictly OTM relative to the Supertrend
    reference price at signal time.
  - Among available OTM strikes, choose the nearest strike to that
    Supertrend reference price.
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
    otm_distance: float = 0.0,
) -> Optional[OptionQuote]:
    """
    Select the nearest available STRICTLY OTM strike to `reference_price`.

    For puts, OTM means strike < Supertrend reference price.
    For calls, OTM means strike > Supertrend reference price.

    `otm_distance` is retained for API compatibility, but is intentionally
    not used as a target offset. The exchange option chain is discrete, so
    the nearest actual OTM strike is selected from available quotes.
    """
    candidates = [q for q in quotes if q.option_type == option_type]
    if not candidates:
        return None

    if option_type == "put":
        otm_candidates = [q for q in candidates if q.strike < reference_price]
    elif option_type == "call":
        otm_candidates = [q for q in candidates if q.strike > reference_price]
    else:
        raise ValueError(f"Unknown option_type: {option_type}")

    if not otm_candidates:
        return None

    return min(otm_candidates, key=lambda q: abs(q.strike - reference_price))
