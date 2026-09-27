"""What to do when the 0DTE short option expires (the roll / take-profit rule).

The start-up position is a directional covered leg:
  BULLISH -> LONG future  + short CALL
  BEARISH -> SHORT future + short PUT

When that option expires (same-day, 0DTE) we look at where BTC is *relative to the
price the future was opened at* and decide:

  BULLISH
    1. price ABOVE the future entry  -> the future leg is in profit: take it.
       CLOSE the future, cancel its SL, and stop (the option already settled).
    2. price NOT above              -> BTC fell; keep the trend trade alive and
       sell a new CALL struck at the *future entry price* (now OTM, since price
       dropped below it) to keep harvesting premium while we wait for recovery.

  BEARISH (mirror)
    1. price BELOW the future entry  -> CLOSE the future + cancel SL.
    2. price NOT below              -> BTC rose; sell a new PUT struck at the
       future entry price (now OTM, since price rose above it).

`decide_on_expiry` is pure (no I/O) so it can be unit-tested and reused by both
the live path and the simulation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExpiryDecision:
    action: str                 # "CLOSE_ALL" | "ROLL"
    reason: str
    opt_type: str | None = None # for ROLL: "CALL" | "PUT"
    strike: int | None = None   # for ROLL: strike of the new short option


def _round_strike(price: float, step: int) -> int:
    return int(round(price / step) * step)


def decide_on_expiry(bias: str, future_entry: float, price: float,
                     strike_step: int = 200) -> ExpiryDecision:
    """Decide the action at a 0DTE expiry. `price` is the current BTC index;
    `future_entry` is the index level the future was opened at."""
    entry_strike = _round_strike(future_entry, strike_step)

    if bias == "BULLISH":
        if price > future_entry:
            return ExpiryDecision("CLOSE_ALL",
                                  f"price {price:,.1f} > future entry {future_entry:,.1f} "
                                  "— take the long future's profit")
        return ExpiryDecision("ROLL",
                              f"price {price:,.1f} <= future entry {future_entry:,.1f} "
                              "— sell a new CALL at the entry strike (now OTM)",
                              opt_type="CALL", strike=entry_strike)

    if bias == "BEARISH":
        if price < future_entry:
            return ExpiryDecision("CLOSE_ALL",
                                  f"price {price:,.1f} < future entry {future_entry:,.1f} "
                                  "— take the short future's profit")
        return ExpiryDecision("ROLL",
                              f"price {price:,.1f} >= future entry {future_entry:,.1f} "
                              "— sell a new PUT at the entry strike (now OTM)",
                              opt_type="PUT", strike=entry_strike)

    raise ValueError(f"unknown bias {bias!r}")
