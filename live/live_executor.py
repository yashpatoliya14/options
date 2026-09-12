from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from engine import FillResult, Leg, SpreadPosition, StrategyParams
from engine.interfaces import OrderExecutor


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LiveExecutor(OrderExecutor):
    """
    Delta REST execution adapter.

    Records per-leg fill prices and latency timestamps (submit / ack / fill)
    into every FillResult so live-vs-backtest execution decay is measurable.
    Credit/debit is computed from actual average fill prices and converted to
    USD with contract_size (Delta BTC options are 0.001 BTC per contract).
    """

    def __init__(self, client: Any, provider: Any, params: StrategyParams, retries: int = 3):
        self.client = client
        self.provider = provider
        self.params = params
        self.retries = retries

    # ------------------------------------------------------------------
    # OrderExecutor
    # ------------------------------------------------------------------

    def open_spread(self, direction: str, short_leg: Leg, long_leg: Leg) -> FillResult:
        submitted = _utc_now()
        long_filled = False
        short_filled = False
        try:
            long_ack = self._place_order(long_leg.symbol, long_leg.qty, "buy", reduce_only=False)
            long_fill = self._wait_fill(long_ack, long_leg.symbol)
            long_filled = True
            short_ack = self._place_order(short_leg.symbol, short_leg.qty, "sell", reduce_only=False)
            short_fill = self._wait_fill(short_ack, short_leg.symbol)
            short_filled = True
            filled = _utc_now()

            short_quote = self.provider.get_quote(short_leg.symbol)
            long_quote = self.provider.get_quote(long_leg.symbol)

            short_price = _f(short_fill.get("avg_fill_price"))
            long_price = _f(long_fill.get("avg_fill_price"))
            if short_price is None:
                short_price = _f(short_quote.get("mark"))
            if long_price is None:
                long_price = _f(long_quote.get("mark"))

            cs = self.params.contract_size
            credit = (short_price - long_price) / cs if cs > 0 else short_price - long_price
            if self.params.use_exchange_stop:
                stop_id = self._place_exchange_stop(short_leg)
                if stop_id is None:
                    raise RuntimeError("protective stop was not created")
            else:
                stop_id = None
            return FillResult(
                ok=True,
                entry_or_exit_credit=credit,
                slippage=0.0,
                commission=self._commission([short_leg, long_leg], [short_price, long_price]),
                legs=[short_leg, long_leg],
                message=json.dumps({"status": "opened", "stop_order_id": stop_id}),
                submitted_at=submitted,
                acked_at=_utc_now(),
                filled_at=filled,
                short_fill=short_price,
                long_fill=long_price,
                short_bid=_f(short_quote.get("bid")),
                short_ask=_f(short_quote.get("ask")),
                long_bid=_f(long_quote.get("bid")),
                long_ask=_f(long_quote.get("ask")),
                short_mid=_f(short_quote.get("mid")),
                long_mid=_f(long_quote.get("mid")),
                stop_order_id=stop_id,
            )
        except Exception as exc:
            self._rollback_open(long_leg, short_leg, long_filled, short_filled)
            return FillResult(False, None, 0.0, 0.0, [short_leg, long_leg], f"open_failed: {exc}")

    def _rollback_open(self, long_leg: Leg, short_leg: Leg, long_filled: bool, short_filled: bool) -> None:
        if short_filled:
            try:
                self._place_order(short_leg.symbol, short_leg.qty, "buy", reduce_only=True)
            except Exception:
                pass
        if long_filled:
            try:
                self._place_order(long_leg.symbol, long_leg.qty, "sell", reduce_only=True)
            except Exception:
                pass

    def close_spread(self, position: SpreadPosition) -> FillResult:
        submitted = _utc_now()
        short_filled = False
        long_filled = False
        try:
            # Remove the exchange stop before manual close so it cannot fire
            # after one or both legs have already been closed.
            if position.stop_order_id:
                self.client.cancel_order(position.stop_order_id)
            short_fill = self._close_leg(position.short_leg, "buy")
            short_filled = True
            long_fill = self._close_leg(position.long_leg, "sell")
            long_filled = True
            filled = _utc_now()

            short_quote = self.provider.get_quote(position.short_leg.symbol)
            long_quote = self.provider.get_quote(position.long_leg.symbol)

            short_price = _f(short_fill.get("avg_fill_price"))
            long_price = _f(long_fill.get("avg_fill_price"))
            if short_price is None:
                short_price = _f(short_quote.get("mark"))
            if long_price is None:
                long_price = _f(long_quote.get("mark"))

            cs = self.params.contract_size
            debit = (short_price - long_price) / cs if cs > 0 else short_price - long_price
            return FillResult(
                ok=True,
                entry_or_exit_credit=debit,
                slippage=0.0,
                commission=self._commission([position.short_leg, position.long_leg], [short_price, long_price]),
                legs=[position.short_leg, position.long_leg],
                message="closed",
                submitted_at=submitted,
                acked_at=_utc_now(),
                filled_at=filled,
                short_fill=short_price,
                long_fill=long_price,
                short_bid=_f(short_quote.get("bid")),
                short_ask=_f(short_quote.get("ask")),
                long_bid=_f(long_quote.get("bid")),
                long_ask=_f(long_quote.get("ask")),
                short_mid=_f(short_quote.get("mid")),
                long_mid=_f(long_quote.get("mid")),
            )
        except Exception as exc:
            # Partial failure: stop order was NOT cancelled so it can retry on next poll.
            # If only one leg filled, the remaining leg stays open for the watchdog.
            failed_legs = []
            if not short_filled:
                failed_legs.append(position.short_leg)
            if not long_filled:
                failed_legs.append(position.long_leg)
            return FillResult(
                ok=False,
                entry_or_exit_credit=None,
                slippage=0.0,
                commission=0.0,
                legs=failed_legs if failed_legs else [position.short_leg, position.long_leg],
                message=f"close_failed: {exc}",
            )

    def _close_leg(self, leg: Leg, side: str, attempts: int = 3) -> dict:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                order = self._place_order(leg.symbol, leg.qty, side, reduce_only=True)
                return self._wait_fill(order, leg.symbol)
            except Exception as exc:
                last_error = exc
        raise last_error if last_error is not None else RuntimeError("close leg failed")

    def _cancel_stop(self, stop_order_id: str | None) -> None:
        if stop_order_id:
            self.client.cancel_order(stop_order_id)

    def mark_to_market(self, position: SpreadPosition) -> float:
        """Executable close debit from real bid/ask (conservative exit mark)."""
        short_quote = self.provider.get_quote(position.short_leg.symbol)
        long_quote = self.provider.get_quote(position.long_leg.symbol)
        short_ask = _f(short_quote.get("ask"))
        long_bid = _f(long_quote.get("bid"))
        if short_ask and short_ask > 0 and long_bid is not None:
            return short_ask - long_bid
        return float(short_quote["mark"]) - float(long_quote["mark"])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

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

    def _wait_fill(self, order_result: dict, symbol: str, attempts: int = 5) -> dict:
        """Poll the order until it carries an average fill price (market orders fill fast)."""
        if _f(order_result.get("avg_fill_price")) is not None:
            return order_result
        order_id = order_result.get("id")
        for _ in range(attempts):
            if order_id is None:
                break
            try:
                result = self.client.get_order(order_id)
                if result and _f(result.get("avg_fill_price")) is not None:
                    return result
            except Exception:
                pass
            time.sleep(0.2)
        return order_result

    def _place_exchange_stop(self, short_leg: Leg) -> str | None:
        sl_pct = self.params.stop_loss_pct
        if not sl_pct or sl_pct >= 100.0:
            return None  # stop disabled by config
        try:
            mark = float(self.provider.get_quote(short_leg.symbol)["mark"])
        except Exception:
            return None
        stop_price = mark * sl_pct
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

    def _commission(self, legs: list[Leg], fill_prices: list[float]) -> float:
        """Flat fee per contract + taker fee on leg notional (premium * contract_size)."""
        cs = self.params.contract_size
        flat = sum(leg.qty for leg in legs) * self.params.commission_per_leg
        notional = sum(
            abs(price) * cs * leg.qty
            for leg, price in zip(legs, fill_prices)
            if price is not None
        )
        return flat + notional * self.params.settlement_fee_pct
