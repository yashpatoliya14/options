from __future__ import annotations

from datetime import timedelta

from engine import FillResult, Leg, SpreadPosition, StrategyParams
from engine.interfaces import OrderExecutor


class SimulatedExecutor(OrderExecutor):
    """
    Realistic simulated fills.

    Fills are per-leg and price-aware:
      - sell leg fills at bid * (1 - slippage_pct)
      - buy leg fills at ask * (1 + slippage_pct)
    Slippage is measured against the mid, not the mark.
    Fees = flat commission_per_leg per contract + settlement_fee_pct of leg
    notional (premium * contract_size), matching Delta-style taker fees.
    Execution delay shifts the fill timestamp, never the price.
    """

    def __init__(self, provider, params: StrategyParams):
        self.provider = provider
        self.params = params
        self.open_position: SpreadPosition | None = None

    # ------------------------------------------------------------------
    # OrderExecutor
    # ------------------------------------------------------------------

    def open_spread(self, direction: str, short_leg: Leg, long_leg: Leg) -> FillResult:
        submitted = self.provider.clock.now()
        filled_at = submitted + timedelta(seconds=max(self.params.execution_delay_seconds, 0))

        short_quote = self.provider.get_quote(short_leg.symbol)
        long_quote = self.provider.get_quote(long_leg.symbol)

        short_fill = self._sell_fill(short_quote)
        long_fill = self._buy_fill(long_quote)
        if short_fill is None or long_fill is None:
            return FillResult(False, None, 0.0, 0.0, [short_leg, long_leg], "no executable quote")

        credit = short_fill - long_fill
        slippage = self._slippage(short_quote, "sell") + self._slippage(long_quote, "buy")
        commission = self._commission(
            [(short_leg, short_fill), (long_leg, long_fill)],
        )
        return FillResult(
            ok=True,
            entry_or_exit_credit=credit,
            slippage=slippage,
            commission=commission,
            legs=[short_leg, long_leg],
            message="simulated_open",
            submitted_at=submitted,
            acked_at=submitted,
            filled_at=filled_at,
            short_fill=short_fill,
            long_fill=long_fill,
            short_bid=_f(short_quote.get("bid")),
            short_ask=_f(short_quote.get("ask")),
            long_bid=_f(long_quote.get("bid")),
            long_ask=_f(long_quote.get("ask")),
            short_mid=_f(short_quote.get("mid")),
            long_mid=_f(long_quote.get("mid")),
        )

    def close_spread(self, position: SpreadPosition) -> FillResult:
        submitted = self.provider.clock.now()
        filled_at = submitted + timedelta(seconds=max(self.params.execution_delay_seconds, 0))

        short_quote = self.provider.get_quote(position.short_leg.symbol)
        long_quote = self.provider.get_quote(position.long_leg.symbol)

        # Closing: buy back the short at ask, sell the long at bid.
        short_fill = self._buy_fill(short_quote)
        long_fill = self._sell_fill(long_quote)
        if short_fill is None or long_fill is None:
            return FillResult(False, None, 0.0, 0.0, [position.short_leg, position.long_leg], "no executable quote")

        debit = short_fill - long_fill
        slippage = self._slippage(short_quote, "buy") + self._slippage(long_quote, "sell")
        commission = self._commission(
            [(position.short_leg, short_fill), (position.long_leg, long_fill)],
        )
        return FillResult(
            ok=True,
            entry_or_exit_credit=debit,
            slippage=slippage,
            commission=commission,
            legs=[position.short_leg, position.long_leg],
            message="simulated_close",
            submitted_at=submitted,
            acked_at=submitted,
            filled_at=filled_at,
            short_fill=short_fill,
            long_fill=long_fill,
            short_bid=_f(short_quote.get("bid")),
            short_ask=_f(short_quote.get("ask")),
            long_bid=_f(long_quote.get("bid")),
            long_ask=_f(long_quote.get("ask")),
            short_mid=_f(short_quote.get("mid")),
            long_mid=_f(long_quote.get("mid")),
        )

    def mark_to_market(self, position: SpreadPosition) -> float:
        """Executable close debit: ask(short) - bid(long), with slippage."""
        short_quote = self.provider.get_quote(position.short_leg.symbol)
        long_quote = self.provider.get_quote(position.long_leg.symbol)
        short_ask = short_quote.get("ask")
        long_bid = long_quote.get("bid")
        if short_ask is None or long_bid is None:
            # Fall back to mark-based debit.
            return float(short_quote["mark"]) - float(long_quote["mark"])
        slip = self.params.slippage_pct
        return float(short_ask) * (1.0 + slip) - float(long_bid) * (1.0 - slip)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sell_fill(self, quote: dict) -> float | None:
        bid = quote.get("bid")
        if bid is not None and float(bid) > 0:
            return float(bid) * (1.0 - self.params.slippage_pct)
        mark = quote.get("mark")
        if mark is not None:
            return float(mark) * (1.0 - self.params.slippage_pct)
        return None

    def _buy_fill(self, quote: dict) -> float | None:
        ask = quote.get("ask")
        if ask is not None and float(ask) > 0:
            return float(ask) * (1.0 + self.params.slippage_pct)
        mark = quote.get("mark")
        if mark is not None:
            return float(mark) * (1.0 + self.params.slippage_pct)
        return None

    def _mid(self, quote: dict) -> float | None:
        mid = quote.get("mid")
        if mid is not None:
            return float(mid)
        bid, ask = quote.get("bid"), quote.get("ask")
        if bid is not None and ask is not None and float(bid) > 0:
            return (float(bid) + float(ask)) / 2.0
        mark = quote.get("mark")
        return float(mark) if mark is not None else None

    def _slippage(self, quote: dict, side: str) -> float:
        """Negative when selling below mid / buying above mid (per contract, USD)."""
        mid = self._mid(quote)
        fill = self._sell_fill(quote) if side == "sell" else self._buy_fill(quote)
        if mid is None or fill is None:
            return 0.0
        size = self.params.contract_size
        if side == "sell":
            return (fill - mid) * size
        return (mid - fill) * size

    def _commission(self, legs_with_fills: list[tuple[Leg, float]]) -> float:
        flat = sum(leg.qty for leg, _ in legs_with_fills) * self.params.commission_per_leg
        fee_pct = self.params.settlement_fee_pct
        size = self.params.contract_size
        notional_fee = 0.0
        if fee_pct > 0:
            for leg, fill_price in legs_with_fills:
                notional_fee += abs(fill_price) * size * leg.qty * fee_pct
        return flat + notional_fee


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
