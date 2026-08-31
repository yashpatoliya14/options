from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd

from .config_schema import StrategyParams
from .models import Leg, Signal, SpreadCandidate, SpreadPosition


class StrategyEngine:
    def __init__(self, params: StrategyParams):
        self.params = params

    def detect_crossover(self, candles: pd.DataFrame) -> Signal | None:
        closed = self._closed_candles(candles)
        min_bars = max(self.params.ema_fast, self.params.ema_slow) + 1
        if len(closed) < min_bars:
            return None

        close = pd.to_numeric(closed["close"], errors="raise")
        fast = close.ewm(span=self.params.ema_fast, adjust=False).mean()
        slow = close.ewm(span=self.params.ema_slow, adjust=False).mean()

        prev_fast = float(fast.iloc[-2])
        prev_slow = float(slow.iloc[-2])
        now_fast = float(fast.iloc[-1])
        now_slow = float(slow.iloc[-1])

        direction = None
        if prev_fast <= prev_slow and now_fast > now_slow:
            direction = "bull"
        elif prev_fast >= prev_slow and now_fast < now_slow:
            direction = "bear"
        if direction is None:
            return None

        adx = self._latest_adx(closed)
        if self.params.adx_min > 0:
            if adx is None or adx < self.params.adx_min:
                return None

        return Signal(
            timestamp=self._to_datetime(closed["timestamp"].iloc[-1]),
            direction=direction,
            fast_ema=now_fast,
            slow_ema=now_slow,
            adx=adx,
        )

    def select_expiry_and_spread(
        self,
        signal: Signal,
        chain_by_expiry: dict[str, pd.DataFrame],
    ) -> SpreadCandidate | None:
        option_type = "put" if signal.direction == "bull" else "call"
        expiries = list(chain_by_expiry.keys())
        for index, expiry in enumerate(expiries[:2]):
            candidate = self._select_for_expiry(
                signal=signal,
                expiry=expiry,
                chain=chain_by_expiry[expiry],
                option_type=option_type,
                expiry_label="0dte" if index == 0 else "next_day",
            )
            if candidate is not None:
                return candidate
        return None

    def should_cut_and_reenter(self, position: SpreadPosition, new_signal: Signal) -> bool:
        return position.direction != new_signal.direction

    def should_close(
        self,
        position: SpreadPosition,
        current_mark: float,
    ) -> Literal["profit_target", "stop_loss"] | None:
        if current_mark <= position.entry_credit * (1.0 - self.params.tp_pct):
            return "profit_target"
        if current_mark >= position.entry_credit * self.params.sl_pct:
            return "stop_loss"
        return None

    def apply_cooldown(self, last_close_time: datetime, now: datetime) -> bool:
        return (now - last_close_time).total_seconds() < self.params.cooldown_seconds

    def _select_for_expiry(
        self,
        signal: Signal,
        expiry: str,
        chain: pd.DataFrame,
        option_type: str,
        expiry_label: str,
    ) -> SpreadCandidate | None:
        if chain.empty:
            return None
        normalized = chain.copy()
        normalized["option_type"] = normalized["option_type"].str.lower()
        normalized = normalized[normalized["option_type"] == option_type].copy()
        if normalized.empty:
            return None

        normalized["strike"] = pd.to_numeric(normalized["strike"], errors="raise")
        normalized["mark"] = self._price_series(normalized)
        spot = self._spot(normalized)
        if spot is not None:
            if signal.direction == "bull":
                normalized = normalized[normalized["strike"] < spot]
            else:
                normalized = normalized[normalized["strike"] > spot]
        if normalized.empty:
            return None

        short_sort_ascending = signal.direction == "bear"
        shorts = normalized.sort_values("strike", ascending=short_sort_ascending)
        for _, short in shorts.iterrows():
            long_strike = (
                float(short["strike"]) - self.params.spread_width
                if signal.direction == "bull"
                else float(short["strike"]) + self.params.spread_width
            )
            long_matches = normalized[normalized["strike"] == long_strike]
            if long_matches.empty:
                continue
            long = long_matches.iloc[0]
            raw_credit = float(short["mark"]) - float(long["mark"])
            net_credit = raw_credit * (1.0 - self.params.slippage_pct)
            if self.params.credit_min <= net_credit <= self.params.credit_max:
                return SpreadCandidate(
                    direction=signal.direction,
                    expiry=expiry,
                    expiry_label=expiry_label,  # type: ignore[arg-type]
                    short_leg=self._leg(short, "sell", expiry, option_type),
                    long_leg=self._leg(long, "buy", expiry, option_type),
                    net_credit=net_credit,
                    width=abs(float(short["strike"]) - float(long["strike"])),
                )
        return None

    def _closed_candles(self, candles: pd.DataFrame) -> pd.DataFrame:
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(candles.columns)
        if missing:
            raise ValueError(f"missing candle columns: {sorted(missing)}")
        closed = candles
        if "closed" in closed.columns:
            closed = closed[closed["closed"].astype(bool)]
        if "is_closed" in closed.columns:
            closed = closed[closed["is_closed"].astype(bool)]
        return closed.sort_values("timestamp").reset_index(drop=True)

    def _latest_adx(self, candles: pd.DataFrame) -> float | None:
        if "adx" in candles.columns:
            value = candles["adx"].iloc[-1]
            return None if pd.isna(value) else float(value)
        if self.params.adx_min <= 0:
            return None
        period = self.params.adx_period
        if len(candles) < period + 2:
            return None

        high = pd.to_numeric(candles["high"], errors="raise")
        low = pd.to_numeric(candles["low"], errors="raise")
        close = pd.to_numeric(candles["close"], errors="raise")
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(period).mean()
        plus_di = 100 * plus_dm.rolling(period).mean() / atr
        minus_di = 100 * minus_dm.rolling(period).mean() / atr
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di)) * 100
        adx = dx.rolling(period).mean().iloc[-1]
        return None if pd.isna(adx) else float(adx)

    def _price_series(self, chain: pd.DataFrame) -> pd.Series:
        for column in ("mark", "mid", "premium"):
            if column in chain.columns:
                return pd.to_numeric(chain[column], errors="raise")
        if {"bid", "ask"}.issubset(chain.columns):
            return (
                pd.to_numeric(chain["bid"], errors="raise")
                + pd.to_numeric(chain["ask"], errors="raise")
            ) / 2.0
        raise ValueError("option chain must include mark/mid/premium or bid+ask")

    def _spot(self, chain: pd.DataFrame) -> float | None:
        for column in ("underlying_price", "spot", "index_price"):
            if column in chain.columns and not chain[column].dropna().empty:
                return float(chain[column].dropna().iloc[0])
        return None

    def _leg(self, row: pd.Series, side: str, expiry: str, option_type: str) -> Leg:
        symbol = row["symbol"] if "symbol" in row and pd.notna(row["symbol"]) else ""
        product_id = None
        if "product_id" in row and pd.notna(row["product_id"]):
            product_id = int(row["product_id"])
        return Leg(
            symbol=str(symbol),
            option_type=option_type,  # type: ignore[arg-type]
            strike=float(row["strike"]),
            expiry=expiry,
            side=side,  # type: ignore[arg-type]
            qty=self.params.qty,
            product_id=product_id,
        )

    def _to_datetime(self, value: object) -> datetime:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.to_pydatetime()
