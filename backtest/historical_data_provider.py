from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

import pandas as pd

from engine import StrategyParams
from engine.interfaces import Clock, DataProvider


class SimulatedClock(Clock):
    def __init__(self, start: datetime):
        self._now = self._as_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = self._as_utc(value)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.to_pydatetime()


class HistoricalDataProvider(DataProvider):
    """
    Point-in-time data provider for backtests.

    Historical option chains are reconstructed with Black-Scholes from the
    visible underlying candle as of clock.now(). Delta does not provide
    historical option-chain snapshots, so this intentionally reports
    data_mode='reconstructed' through the runner's TradeRecord output.
    """

    def __init__(
        self,
        candles: pd.DataFrame,
        clock: SimulatedClock,
        params: StrategyParams,
        strike_count_each_side: int = 20,
    ):
        self.clock = clock
        self.params = params
        self.strike_count_each_side = strike_count_each_side
        self.candles = self._normalize_candles(candles)
        self._timestamp_series = self.candles["timestamp"]
        self.last_candle_query_max_timestamp: datetime | None = None

    @classmethod
    def from_csv(
        cls,
        path: str,
        clock: SimulatedClock,
        params: StrategyParams,
        timestamp_column: str = "timestamp",
    ) -> "HistoricalDataProvider":
        candles = pd.read_csv(path)
        if timestamp_column != "timestamp":
            candles = candles.rename(columns={timestamp_column: "timestamp"})
        return cls(candles, clock, params)

    def get_candles(self, symbol: str, resolution: str, lookback: int) -> pd.DataFrame:
        now = self.clock.now()
        idx = self._timestamp_series.searchsorted(pd.Timestamp(now), side='right')
        start_idx = max(0, idx - lookback) if lookback > 0 else 0
        visible = self.candles.iloc[start_idx:idx].copy()
        if not visible.empty:
            self.last_candle_query_max_timestamp = visible["timestamp"].max().to_pydatetime()
        return visible.reset_index(drop=True)

    def get_available_expiries(self, underlying: str) -> list[str]:
        today = self.clock.now().date()
        return [today.isoformat(), (today + timedelta(days=1)).isoformat()]

    def get_option_chain(self, underlying: str, expiry_date: str) -> pd.DataFrame:
        now = self.clock.now()
        spot = self._spot_as_of(now)
        expiry = self._expiry_datetime(expiry_date)
        t_years = max((expiry - now).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)
        step = self._strike_step(spot)
        rows = []

        center = round(spot / step) * step
        for offset in range(-self.strike_count_each_side, self.strike_count_each_side + 1):
            strike = center + offset * step
            if strike <= 0:
                continue
            for option_type in ("put", "call"):
                mark = self._bs_price(
                    spot=spot,
                    strike=strike,
                    t_years=t_years,
                    iv=self.params.assumed_iv,
                    rate=self.params.risk_free_rate,
                    option_type=option_type,
                )
                rows.append(
                    {
                        "symbol": f"{option_type[0].upper()}-{underlying}-{int(strike)}-{expiry_date}",
                        "option_type": option_type,
                        "strike": float(strike),
                        "bid": max(mark * 0.995, 0.0),
                        "ask": mark * 1.005,
                        "mark": mark,
                        "iv": self.params.assumed_iv,
                        "delta": None,
                        "gamma": None,
                        "theta": None,
                        "vega": None,
                        "product_id": None,
                        "underlying_price": spot,
                        "timestamp": now,
                    }
                )
        return pd.DataFrame(rows)

    def get_quote(self, symbol: str) -> dict:
        parsed = self._parse_symbol(symbol)
        now = self.clock.now()
        spot = self._spot_as_of(now)
        expiry = self._expiry_datetime(parsed["expiry"])
        t_years = max((expiry - now).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)
        mark = self._bs_price(
            spot=spot,
            strike=parsed["strike"],
            t_years=t_years,
            iv=self.params.assumed_iv,
            rate=self.params.risk_free_rate,
            option_type=parsed["option_type"],
        )
        return {"mark": mark}

    def _spot_as_of(self, now: datetime) -> float:
        idx = self._timestamp_series.searchsorted(pd.Timestamp(now), side='right')
        if idx == 0:
            raise ValueError("no candle is visible at simulated clock time")
        return float(self.candles.iloc[idx - 1]["close"])

    @staticmethod
    def _normalize_candles(candles: pd.DataFrame) -> pd.DataFrame:
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(candles.columns)
        if missing:
            raise ValueError(f"missing candle columns: {sorted(missing)}")
        normalized = candles.copy()
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            normalized[column] = pd.to_numeric(normalized[column], errors="raise")
        return normalized.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _expiry_datetime(expiry_date: str) -> datetime:
        return datetime.fromisoformat(expiry_date).replace(
            hour=17,
            minute=30,
            second=0,
            microsecond=0,
            tzinfo=timezone.utc,
        )

    @staticmethod
    def _strike_step(spot: float) -> float:
        return float(max(round(spot * 0.01 / 50.0) * 50, 50))

    @staticmethod
    def _bs_price(
        spot: float,
        strike: float,
        t_years: float,
        iv: float,
        rate: float,
        option_type: str,
    ) -> float:
        if spot <= 0 or strike <= 0:
            return 0.0
        if t_years <= 0:
            if option_type == "call":
                return max(0.0, spot - strike)
            return max(0.0, strike - spot)

        d1 = (math.log(spot / strike) + (rate + 0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
        d2 = d1 - iv * math.sqrt(t_years)
        normal = NormalDist()
        if option_type == "call":
            return spot * normal.cdf(d1) - strike * math.exp(-rate * t_years) * normal.cdf(d2)
        return strike * math.exp(-rate * t_years) * normal.cdf(-d2) - spot * normal.cdf(-d1)

    @staticmethod
    def _parse_symbol(symbol: str) -> dict:
        parts = symbol.split("-")
        if len(parts) < 4:
            raise ValueError(f"cannot parse option symbol: {symbol}")
        option_type = "put" if parts[0] == "P" else "call"
        return {
            "option_type": option_type,
            "underlying": parts[1],
            "strike": float(parts[2]),
            "expiry": "-".join(parts[3:]),
        }
