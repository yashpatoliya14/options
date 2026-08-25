"""
Expiry selection.

Confirmed rule (spec section 6):
  1. Try today's expiry. If the selected strike's premium >= MIN_PREMIUM, use it.
  2. Else try tomorrow's expiry. If premium >= MIN_PREMIUM, use it.
  3. Else: no trade. Do NOT walk further into future expiries without explicit
     approval (spec is explicit about this).

This function is the SAME one used by backtest.py (via SimulatedBroker) and
live_algo.py (via RealBroker/PaperBroker) -- it only depends on the Broker
interface, not on which implementation is behind it.
"""
from dataclasses import dataclass
from typing import Optional

from broker import Broker, OptionQuote
from strategy.option_selector import select_strike


@dataclass
class ExpirySelectionResult:
    quote: Optional[OptionQuote]
    expiry_used: Optional[str]   # 'today' | 'tomorrow' | None
    rejected_reason: Optional[str] = None
    best_premium_seen: Optional[float] = None
    best_expiry_seen: Optional[str] = None


def select_expiry_and_strike(
    broker: Broker,
    underlying: str,
    supertrend_value: float,
    spot_price: float,
    option_type: str,
    min_premium: float,
    as_of_timestamp: Optional[int] = None,
) -> ExpirySelectionResult:
    expiries = broker.get_available_expiries(underlying, as_of_timestamp=as_of_timestamp)
    if not expiries:
        return ExpirySelectionResult(None, None, "no_expiries_available")

    best_premium_seen: Optional[float] = None
    best_expiry_seen: Optional[str] = None

    for i, expiry in enumerate(expiries):
        label = "today" if i == 0 else "tomorrow" if i == 1 else f"day_{i}"
        
        quotes = broker.get_option_chain(underlying, expiry, timestamp=as_of_timestamp)
        # Pass min_premium down so we pick the closest strike that ACTUALLY meets the minimum premium.
        best = select_strike(quotes, supertrend_value, spot_price, option_type, min_premium=min_premium)
        if best is None:
            continue
            
        if best_premium_seen is None or best.premium > best_premium_seen:
            best_premium_seen = best.premium
            best_expiry_seen = label
            
        return ExpirySelectionResult(best, label)

    return ExpirySelectionResult(
        None,
        None,
        "min_premium_not_met_in_any_expiry",
        best_premium_seen=best_premium_seen,
        best_expiry_seen=best_expiry_seen,
    )
