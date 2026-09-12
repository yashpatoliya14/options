from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from engine.interfaces import Clock, DataProvider


class WallClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # drop NaN


class LiveDataProvider(DataProvider):
    """Delta REST market-data adapter returning real bid/ask and greeks."""

    def __init__(self, client: Any, clock: Clock):
        self.client = client
        self.clock = clock

    def get_candles(self, symbol: str, resolution: str, lookback: int) -> pd.DataFrame:
        now = self.clock.now()
        seconds = self._resolution_seconds(resolution)
        start = int((now - timedelta(seconds=seconds * max(lookback, 1) * 2)).timestamp())
        end = int(now.timestamp())
        rows = self.client.get_historical_candles(symbol, resolution, start, end)
        candles = pd.DataFrame(rows)
        if candles.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        if "time" in candles.columns and "timestamp" not in candles.columns:
            candles = candles.rename(columns={"time": "timestamp"})
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], unit="s", utc=True, errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            candles[column] = pd.to_numeric(candles[column], errors="raise")
        now_ts = pd.Timestamp(now)
        close_at = candles["timestamp"] + pd.to_timedelta(seconds, unit="s")
        candles = candles[close_at <= now_ts]
        return candles.sort_values("timestamp").tail(lookback).reset_index(drop=True)

    def get_available_expiries(self, underlying: str) -> list[str]:
        now = self.clock.now()
        expiries = self.client.get_available_expiries(underlying)
        return [expiry for expiry in expiries if datetime.fromisoformat(expiry).date() >= now.date()]

    def get_option_chain(self, underlying: str, expiry_date: str) -> pd.DataFrame:
        quotes = self.client.get_option_chain(underlying, expiry_date)
        rows = []
        for index, quote in enumerate(quotes, start=1):
            if isinstance(quote, dict):
                option_type = "call" if "call" in quote.get("contract_type", "") else "put"
                raw = quote.get("quotes", {}) or {}
                bid = _f(raw.get("best_bid"))
                ask = _f(raw.get("best_ask"))
                mark = _f(quote.get("mark_price")) or _f(quote.get("close"))
                if mark is None:
                    mark = bid or ask or 0.0
                strike = _f(quote.get("strike_price")) or _f(quote.get("strike")) or 0.0
                symbol = quote.get("symbol", "")
                underlying_price = _f(quote.get("spot_price")) or _f(quote.get("underlying_price")) or 0.0
                greeks = {
                    "iv": _f(quote.get("iv")),
                    "delta": _f(quote.get("delta")),
                    "gamma": _f(quote.get("gamma")),
                    "theta": _f(quote.get("theta")),
                    "vega": _f(quote.get("vega")),
                }
            else:
                option_type = quote.option_type
                bid = _f(quote.bid)
                ask = _f(quote.ask)
                mark = _f(quote.mark)
                strike = _f(quote.strike) or 0.0
                symbol = quote.symbol
                underlying_price = _f(quote.underlying_price) or 0.0
                greeks = {
                    "iv": _f(getattr(quote, "iv", None)),
                    "delta": _f(getattr(quote, "delta", None)),
                    "gamma": _f(getattr(quote, "gamma", None)),
                    "theta": _f(getattr(quote, "theta", None)),
                    "vega": _f(getattr(quote, "vega", None)),
                }
            rows.append(
                {
                    "symbol": symbol,
                    "option_type": option_type,
                    "strike": float(strike),
                    "bid": float(bid) if bid is not None else 0.0,
                    "ask": float(ask) if ask is not None else 0.0,
                    "mark": float(mark) if mark is not None else 0.0,
                    "mid": (
                        (float(bid) + float(ask)) / 2.0
                        if bid is not None and ask is not None and bid > 0 and ask > 0
                        else (float(mark) if mark is not None else 0.0)
                    ),
                    "volume": _f(raw.get("volume")) if isinstance(quote, dict) else None,
                    "open_interest": _f(raw.get("open_interest")) if isinstance(quote, dict) else None,
                    **greeks,
                    "product_id": index,
                    "underlying_price": float(underlying_price),
                    "timestamp": self.clock.now(),
                }
            )
        return pd.DataFrame(rows)

    def get_quote(self, symbol: str) -> dict[str, Any]:
        ticker = self.client.get_ticker(symbol)
        raw = ticker.get("quotes", {}) if isinstance(ticker, dict) else {}
        bid = _f(raw.get("best_bid"))
        ask = _f(raw.get("best_ask"))
        mark = _f(ticker.get("mark_price")) if isinstance(ticker, dict) else None
        if mark is None:
            mark = bid if bid is not None else ask
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None and bid > 0 and ask > 0 else mark
        return {
            "symbol": symbol,
            "bid": float(bid) if bid is not None else 0.0,
            "ask": float(ask) if ask is not None else 0.0,
            "mark": float(mark) if mark is not None else 0.0,
            "mid": float(mid) if mid is not None else 0.0,
            "iv": _f(ticker.get("iv")) if isinstance(ticker, dict) else None,
            "delta": _f(ticker.get("delta")) if isinstance(ticker, dict) else None,
            "gamma": _f(ticker.get("gamma")) if isinstance(ticker, dict) else None,
            "theta": _f(ticker.get("theta")) if isinstance(ticker, dict) else None,
            "vega": _f(ticker.get("vega")) if isinstance(ticker, dict) else None,
            "timestamp": self.clock.now(),
        }

    @staticmethod
    def _resolution_seconds(resolution: str) -> int:
        value = resolution.strip().lower()
        if value.endswith("m"):
            return int(value[:-1]) * 60
        if value.endswith("h"):
            return int(value[:-1]) * 3600
        if value.endswith("d"):
            return int(value[:-1]) * 86400
        return int(value)
