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
    supertrend_value: float,
    spot_price: float,
    option_type: str,
    min_premium: float = 0.0,
) -> Optional[OptionQuote]:
    """
    From a list of quotes (already filtered to one expiry), pick the one of
    `option_type` that is Out-of-The-Money (OTM), has premium >= min_premium, 
    and whose strike is closest to the Supertrend line (`supertrend_value`).
    
    For a PUT: OTM means Strike < Spot Price.
    For a CALL: OTM means Strike > Spot Price.
    """
    candidates = [q for q in quotes if q.option_type == option_type]
    if not candidates:
        return None
        
    otm_candidates = []
    for q in candidates:
        if q.premium < min_premium:
            continue
        if option_type == "put" and q.strike < spot_price:
            otm_candidates.append(q)
        elif option_type == "call" and q.strike > spot_price:
            otm_candidates.append(q)
            
    if not otm_candidates:
        return None
        
    # We want it to be close to supertrend, but not infinitely far. 
    # Increased to 15% to prevent skipped trades in high volatility.
    valid_candidates = [q for q in otm_candidates if abs(q.strike - supertrend_value) <= (supertrend_value * 0.15)]
    if not valid_candidates:
        return None
        
    # Select the OTM strike closest to the Supertrend value
    return min(valid_candidates, key=lambda q: abs(q.strike - supertrend_value))

