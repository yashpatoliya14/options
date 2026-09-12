from datetime import datetime, timezone

from engine import Leg, SpreadCandidate, SpreadPosition, StrategyParams
from live.live_executor import LiveExecutor


class FakeProvider:
    def get_quote(self, symbol):
        return {"mark": 10.0, "bid": 9.5, "ask": 10.5}


class FakeClient:
    def __init__(self):
        self.posts = []
        self.cancelled = []

    def _post(self, path, body):
        self.posts.append((path, body))
        if path == "/v2/orders/bracket":
            return {"result": {"id": "stop-1"}}
        price = 12.0 if body["side"] == "sell" else 8.0
        return {"result": {"id": f"order-{len(self.posts)}", "avg_fill_price": price}}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"id": order_id}


def _position():
    expiry = "2026-09-30"
    short = Leg("C-BTC-SHORT", "call", 100000, expiry, "sell", 1)
    long = Leg("C-BTC-LONG", "call", 100200, expiry, "buy", 1)
    candidate = SpreadCandidate("bear", expiry, "next_day", short, long, 4000.0, 200.0)
    return SpreadPosition.from_candidate(
        candidate,
        datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        entry_credit=4000.0,
        stop_order_id="stop-1",
    )


def test_open_records_protective_stop_id_and_close_cancels_it():
    client = FakeClient()
    executor = LiveExecutor(client, FakeProvider(), StrategyParams(use_exchange_stop=True))
    position = _position()

    fill = executor.open_spread("bear", position.short_leg, position.long_leg)
    assert fill.ok is True
    assert fill.stop_order_id == "stop-1"

    close = executor.close_spread(position)
    assert close.ok is True
    assert client.cancelled == ["stop-1"]
    assert [body["side"] for path, body in client.posts if path == "/v2/orders"] == [
        "buy",
        "sell",
        "buy",
        "sell",
    ]
