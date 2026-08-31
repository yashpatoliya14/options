from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from engine.interfaces import Clock, DataProvider


class WallClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class LiveDataProvider(DataProvider):
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
        candles = candles[candles["timestamp"] <= pd.Timestamp(now)]
        return candles.sort_values("timestamp").tail(lookback).reset_index(drop=True)

    def get_available_expiries(self, underlying: str) -> list[str]:
        today = self.clock.now().date()
        expiries = self.client.get_available_expiries(underlying)
        return [expiry for expiry in expiries if datetime.fromisoformat(expiry).date() >= today]

    def get_option_chain(self, underlying: str, expiry_date: str) -> pd.DataFrame:
        quotes = self.client.get_option_chain(underlying, expiry_date)
        rows = []
        for index, quote in enumerate(quotes, start=1):
            if isinstance(quote, dict):
                option_type = "call" if "call" in quote.get("contract_type", "") else "put"
                raw_quotes = quote.get("quotes", {})
                premium = raw_quotes.get("best_bid") or quote.get("mark_price") or quote.get("close") or 0
                strike = quote.get("strike_price") or quote.get("strike") or 0
                symbol = quote.get("symbol", "")
                underlying_price = quote.get("spot_price") or quote.get("underlying_price") or 0
            else:
                option_type = quote.option_type
                premium = quote.premium
                strike = quote.strike
                symbol = quote.symbol
                underlying_price = quote.underlying_price
            rows.append(
                {
                    "symbol": symbol,
                    "option_type": option_type,
                    "strike": float(strike),
                    "bid": float(premium),
                    "ask": float(premium),
                    "mark": float(premium),
                    "iv": None,
                    "delta": None,
                    "gamma": None,
                    "theta": None,
                    "vega": None,
                    "product_id": index,
                    "underlying_price": float(underlying_price),
                    "timestamp": self.clock.now(),
                }
            )
        return pd.DataFrame(rows)

    def get_quote(self, symbol: str) -> dict[str, Any]:
        ticker = self.client.get_ticker(symbol)
        quote = ticker.get("quotes", {}) if isinstance(ticker, dict) else {}
        bid = quote.get("best_bid") or ticker.get("mark_price") or ticker.get("close")
        ask = quote.get("best_ask") or ticker.get("mark_price") or ticker.get("close")
        mark = ticker.get("mark_price") or bid
        return {
            "symbol": symbol,
            "bid": float(bid),
            "ask": float(ask),
            "mark": float(mark),
            "iv": ticker.get("iv"),
            "delta": ticker.get("delta"),
            "gamma": ticker.get("gamma"),
            "theta": ticker.get("theta"),
            "vega": ticker.get("vega"),
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
