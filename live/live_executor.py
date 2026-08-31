from __future__ import annotations

import json
import time
from typing import Any

from engine import FillResult, Leg, SpreadPosition, StrategyParams
from engine.interfaces import OrderExecutor


class LiveExecutor(OrderExecutor):
    def __init__(self, client: Any, provider: Any, params: StrategyParams, retries: int = 3):
        self.client = client
        self.provider = provider
        self.params = params
        self.retries = retries

    def open_spread(self, direction: str, short_leg: Leg, long_leg: Leg) -> FillResult:
        try:
            long_fill = self._place_order(long_leg.symbol, long_leg.qty, "buy", reduce_only=False)
            short_fill = self._place_order(short_leg.symbol, short_leg.qty, "sell", reduce_only=False)
            stop_id = self._place_exchange_stop(short_leg)
            short_price = float(short_fill.get("average_fill_price", self.provider.get_quote(short_leg.symbol)["mark"]))
            long_price = float(long_fill.get("average_fill_price", self.provider.get_quote(long_leg.symbol)["mark"]))
            credit = max(short_price - long_price, 0.0)
            return FillResult(
                ok=True,
                entry_or_exit_credit=credit,
                slippage=0.0,
                commission=self._commission([short_leg, long_leg]),
                legs=[short_leg, long_leg],
                message=json.dumps({"status": "opened", "stop_order_id": stop_id}),
            )
        except Exception as exc:
            return FillResult(False, None, 0.0, 0.0, [short_leg, long_leg], f"open_failed: {exc}")

    def close_spread(self, position: SpreadPosition) -> FillResult:
        try:
            short_close = self._place_order(
                position.short_leg.symbol,
                position.short_leg.qty,
                "buy",
                reduce_only=True,
            )
            long_close = self._place_order(
                position.long_leg.symbol,
                position.long_leg.qty,
                "sell",
                reduce_only=True,
            )
            short_price = float(short_close.get("average_fill_price", self.provider.get_quote(position.short_leg.symbol)["mark"]))
            long_price = float(long_close.get("average_fill_price", self.provider.get_quote(position.long_leg.symbol)["mark"]))
            debit = max(short_price - long_price, 0.0)
            return FillResult(
                ok=True,
                entry_or_exit_credit=debit,
                slippage=0.0,
                commission=self._commission([position.short_leg, position.long_leg]),
                legs=[position.short_leg, position.long_leg],
                message="closed",
            )
        except Exception as exc:
            return FillResult(
                False,
                None,
                0.0,
                0.0,
                [position.short_leg, position.long_leg],
                f"close_failed: {exc}",
            )

    def mark_to_market(self, position: SpreadPosition) -> float:
        short_mark = float(self.provider.get_quote(position.short_leg.symbol)["mark"])
        long_mark = float(self.provider.get_quote(position.long_leg.symbol)["mark"])
        return max(short_mark - long_mark, 0.0)

    def _place_order(self, symbol: str, qty: int, side: str, reduce_only: bool) -> dict:
        body = {
            "product_symbol": symbol,
            "size": qty,
            "side": side,
            "order_type": "market_order",
        }
        if reduce_only:
            body["reduce_only"] = True
        response = self._post_with_retries("/v2/orders", body)
        return response.get("result", {})

    def _place_exchange_stop(self, short_leg: Leg) -> str | None:
        stop_price = float(self.provider.get_quote(short_leg.symbol)["mark"]) * self.params.sl_pct
        body = {
            "product_symbol": short_leg.symbol,
            "size": short_leg.qty,
            "side": "buy",
            "order_type": "stop_market_order",
            "stop_price": stop_price,
            "reduce_only": True,
        }
        try:
            response = self._post_with_retries("/v2/orders/bracket", body)
            result = response.get("result", {})
            return str(result.get("id")) if result.get("id") is not None else None
        except Exception:
            return None

    def _post_with_retries(self, path: str, body: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                return self.client._post(path, body)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
        raise last_error if last_error is not None else RuntimeError("post failed")

    def _commission(self, legs: list[Leg]) -> float:
        return sum(leg.qty for leg in legs) * self.params.commission_per_leg
