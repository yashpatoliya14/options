from __future__ import annotations

from engine import FillResult, Leg, SpreadPosition, StrategyParams
from engine.interfaces import OrderExecutor


class SimulatedExecutor(OrderExecutor):
    def __init__(self, provider, params: StrategyParams):
        self.provider = provider
        self.params = params
        self.open_position: SpreadPosition | None = None

    def open_spread(self, direction: str, short_leg: Leg, long_leg: Leg) -> FillResult:
        short_mark = float(self.provider.get_quote(short_leg.symbol)["mark"])
        long_mark = float(self.provider.get_quote(long_leg.symbol)["mark"])
        raw_credit = max(short_mark - long_mark, 0.0)
        slippage = raw_credit * self.params.slippage_pct
        credit = max(raw_credit - slippage, 0.0)
        commission = self._commission([short_leg, long_leg])
        return FillResult(
            ok=True,
            entry_or_exit_credit=credit,
            slippage=slippage,
            commission=commission,
            legs=[short_leg, long_leg],
            message="simulated_open",
        )

    def close_spread(self, position: SpreadPosition) -> FillResult:
        mark = self.mark_to_market(position)
        slippage = mark * self.params.slippage_pct
        debit = mark + slippage
        commission = self._commission([position.short_leg, position.long_leg])
        self.open_position = None
        return FillResult(
            ok=True,
            entry_or_exit_credit=debit,
            slippage=slippage,
            commission=commission,
            legs=[position.short_leg, position.long_leg],
            message="simulated_close",
        )

    def mark_to_market(self, position: SpreadPosition) -> float:
        short_mark = float(self.provider.get_quote(position.short_leg.symbol)["mark"])
        long_mark = float(self.provider.get_quote(position.long_leg.symbol)["mark"])
        return max(short_mark - long_mark, 0.0)

    def _commission(self, legs: list[Leg]) -> float:
        return sum(leg.qty for leg in legs) * self.params.commission_per_leg
