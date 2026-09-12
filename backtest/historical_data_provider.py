from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

import pandas as pd

from engine import StrategyParams
from engine.black_scholes import greeks as bs_greeks, price as bs_price
from engine.indicators import realized_vol, resolution_seconds
from engine.interfaces import Clock, DataProvider


# Asset-specific implied volatilities for Black-Scholes reconstruction
ASSET_IV_DEFAULTS = {
    "BTC": 0.55,
    "ETH": 0.65,
    "XAUT": 0.25,
}

# Asset-specific strike step overrides
ASSET_STRIKE_STEPS = {
    "XAUT": 5.0,    # Gold ~$2600, step=$5
    "ETH": 25.0,    # ETH ~$2500, step=$25
}


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
        self._bar_seconds = resolution_seconds(params.resolution)

        # Resolve asset-specific IV
        underlying = params.underlying.upper()
        self._effective_iv = ASSET_IV_DEFAULTS.get(underlying, params.assumed_iv)
        if params.assumed_iv != 0.55:
            self._effective_iv = params.assumed_iv

        periods_per_year = int((365 * 24 * 3600) / max(self._bar_seconds, 1))
        self.candles["realized_vol"] = realized_vol(
            self.candles["close"],
            params.rv_window,
            periods_per_year,
        )

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
        now_ts = pd.Timestamp(now)
        idx = self._timestamp_series.searchsorted(now_ts, side="right")
        start_idx = max(0, idx - lookback) if lookback > 0 else 0
        visible = self.candles.iloc[start_idx:idx].copy()
        if not visible.empty:
            visible["is_closed"] = True
            self.last_candle_query_max_timestamp = visible["timestamp"].max().to_pydatetime()
        return visible.reset_index(drop=True)

    def get_available_expiries(self, underlying: str) -> list[str]:
        now = self.clock.now()
        today = now.date()
        expiries = [today.isoformat(), (today + timedelta(days=1)).isoformat()]
        # Never return an expiry whose settlement has already passed: an option
        # expiring at 12:30 UTC today cannot be traded at 13:00 UTC.
        return [e for e in expiries if self._expiry_datetime(e) > now]

    def get_expiry_datetime(self, expiry_date: str) -> datetime:
        """Return the actual UTC settlement timestamp for an expiry date."""
        return self._expiry_datetime(expiry_date)

    def get_option_chain(self, underlying: str, expiry_date: str) -> pd.DataFrame:
        now = self.clock.now()
        spot = self._spot_as_of(now)
        expiry = self._expiry_datetime(expiry_date)
        t_years = max((expiry - now).total_seconds(), 0.0) / (365.0 * 24.0 * 3600.0)
        step = self._strike_step(spot, self.params.underlying)
        iv = self._iv_as_of(now)
        half_spread = self.params.bid_ask_spread_pct / 2.0
        rows = []

        center = round(spot / step) * step
        for offset in range(-self.strike_count_each_side, self.strike_count_each_side + 1):
            strike = center + offset * step
            if strike <= 0:
                continue
            for option_type in ("put", "call"):
                g = bs_greeks(
                    spot=spot,
                    strike=strike,
                    t_years=t_years,
                    iv=iv,
                    rate=self.params.risk_free_rate,
                    option_type=option_type,
                )
                mark = g["price"]
                rows.append(
                    {
                        "symbol": f"{option_type[0].upper()}-{underlying}-{int(strike)}-{expiry_date}",
                        "option_type": option_type,
                        "strike": float(strike),
                        "bid": max(mark * (1.0 - half_spread), 0.0),
                        "ask": mark * (1.0 + half_spread),
                        "mark": mark,
                        "iv": iv,
                        "delta": g["delta"],
                        "gamma": g["gamma"],
                        "theta": g["theta"],
                        "vega": g["vega"],
                        "volume": 1000.0,
                        "open_interest": 1000.0,
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
        iv = self._iv_as_of(now)
        g = bs_greeks(
            spot=spot,
            strike=parsed["strike"],
            t_years=t_years,
            iv=iv,
            rate=self.params.risk_free_rate,
            option_type=parsed["option_type"],
        )
        mark = g["price"]
        half_spread = self.params.bid_ask_spread_pct / 2.0
        return {
            "mark": mark,
            "bid": max(mark * (1.0 - half_spread), 0.0),
            "ask": mark * (1.0 + half_spread),
            "mid": mark,
            "iv": iv,
            "delta": g["delta"],
            "gamma": g["gamma"],
            "theta": g["theta"],
            "vega": g["vega"],
            "underlying_price": spot,
        }

    def _iv_as_of(self, now: datetime) -> float:
        if self.params.iv_mode != "realized_scaled":
            return self._effective_iv
        idx = self._timestamp_series.searchsorted(pd.Timestamp(now), side="right")
        if idx == 0:
            return self._effective_iv
        rv = self.candles.iloc[idx - 1].get("realized_vol")
        if rv is None or pd.isna(rv) or rv <= 0:
            return self._effective_iv
        return float(max(min(rv, 3.0), 0.05))

    def _spot_as_of(self, now: datetime) -> float:
        idx = self._timestamp_series.searchsorted(pd.Timestamp(now), side="right")
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
        """
        Delta India option settlement: 12:30 UTC (18:00 IST).
        Supertrend signals fire on the hour; using 12:30 avoids both the
        phantom next-day expiry after 12:30 UTC and the 5.5-hour ASSET
        settlement error the old 17:30 default created.
        """
        return datetime.fromisoformat(expiry_date).replace(
            hour=12,
            minute=30,
            second=0,
            microsecond=0,
            tzinfo=timezone.utc,
        )

    @staticmethod
    def _parse_symbol(symbol: str) -> dict:
        parts = symbol.split("-")
        if len(parts) < 4:
            raise ValueError(f"cannot parse option symbol: {symbol}")
        return {
            "option_type": "put" if parts[0].upper() == "P" else "call",
            "strike": float(parts[2]),
            "expiry": "-".join(parts[3:]),
        }

    @staticmethod
    def _strike_step(spot: float, underlying: str = "BTC") -> float:
        """Calculate strike step, with asset-specific overrides."""
        upper = underlying.upper()
        if upper in ASSET_STRIKE_STEPS:
            return ASSET_STRIKE_STEPS[upper]
        return float(max(round(spot * 0.01 / 50.0) * 50, 50))
