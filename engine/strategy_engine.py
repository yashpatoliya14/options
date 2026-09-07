from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd

from .config_schema import StrategyParams
from .models import Leg, Signal, SpreadCandidate, SpreadPosition


class StrategyEngine:
    """
    Directional options strategy engine.

    Signal generation: ADX (trend strength) + EMA (trend structure) + RSI (momentum).
    Trade execution: Bull Put Spread for buy signals, Bear Put Spread for sell signals.
    """

    def __init__(self, params: StrategyParams):
        self.params = params

    # ------------------------------------------------------------------
    # PUBLIC: Signal detection
    # ------------------------------------------------------------------

    def detect_signal(self, candles: pd.DataFrame) -> Signal | None:
        """
        Multi-indicator directional signal using ADX + EMA + RSI.

        Returns a Signal with direction='bull'/signal_type='buy' or
        direction='bear'/signal_type='sell', or None if no signal.
        """
        closed = self._closed_candles(candles)
        min_bars = max(
            self.params.ema_trend_slow,
            self.params.adx_period * 2 + 2,
            self.params.rsi_period + 2,
        ) + 1
        if len(closed) < min_bars:
            return None

        close = pd.to_numeric(closed["close"], errors="raise")

        # --- 1. ADX Trend Filter ---
        adx = self._latest_adx(closed)
        if adx is None or adx < self.params.adx_trend_threshold:
            return None

        # --- 2. EMA Trend Structure ---
        ema_fast = close.ewm(span=self.params.ema_trend_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.params.ema_trend_slow, adjust=False).mean()

        now_fast = float(ema_fast.iloc[-1])
        now_slow = float(ema_slow.iloc[-1])
        now_price = float(close.iloc[-1])

        if now_fast > now_slow and now_price > now_fast:
            ema_trend = "bullish_structure"
        elif now_fast < now_slow and now_price < now_fast:
            ema_trend = "bearish_structure"
        else:
            return None  # Neutral / choppy

        # --- 3. RSI Momentum Confirmation ---
        rsi_series = self._compute_rsi(close, self.params.rsi_period)
        if rsi_series is None or len(rsi_series) < 2:
            return None

        rsi_now = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2])

        buy_signal = False
        sell_signal = False

        if ema_trend == "bullish_structure":
            # Buy: RSI crosses above oversold OR RSI is 40-65 and rising
            if rsi_prev <= self.params.rsi_oversold and rsi_now > self.params.rsi_oversold:
                buy_signal = True
            elif 40.0 <= rsi_now <= self.params.rsi_overbought and rsi_now > rsi_prev:
                buy_signal = True

        if ema_trend == "bearish_structure":
            # Sell: RSI crosses below overbought OR RSI is 35-60 and falling
            if rsi_prev >= self.params.rsi_overbought and rsi_now < self.params.rsi_overbought:
                sell_signal = True
            elif self.params.rsi_oversold <= rsi_now <= 60.0 and rsi_now < rsi_prev:
                sell_signal = True

        if not buy_signal and not sell_signal:
            return None

        direction = "bull" if buy_signal else "bear"
        signal_type = "buy" if buy_signal else "sell"

        return Signal(
            timestamp=self._to_datetime(closed["timestamp"].iloc[-1]),
            direction=direction,
            fast_ema=now_fast,
            slow_ema=now_slow,
            adx=adx,
            rsi=rsi_now,
            ema_trend=ema_trend,
            signal_type=signal_type,
        )

    def detect_crossover(self, candles: pd.DataFrame) -> Signal | None:
        """Legacy crossover detection — delegates to detect_signal for directional mode."""
        if self.params.spread_type == "directional":
            return self.detect_signal(candles)
        return self._legacy_detect_crossover(candles)

    # ------------------------------------------------------------------
    # PUBLIC: Spread selection
    # ------------------------------------------------------------------

    def select_expiry_and_spread(
        self,
        signal: Signal,
        chain_by_expiry: dict[str, pd.DataFrame],
    ) -> SpreadCandidate | None:
        """
        Select spread based on signal direction.

        Bull signal → Bull Put Spread (credit spread, sell high put, buy low put)
        Bear signal → Bear Put Spread (debit spread, buy high put, sell low put)
        """
        if self.params.spread_type == "directional":
            return self._select_directional_spread(signal, chain_by_expiry)
        return self._legacy_select_spread(signal, chain_by_expiry)

    def get_expiry_type(self, signal_time: datetime) -> str:
        """Determine if signal should use same-day or next-day expiry."""
        hour = signal_time.hour
        if hour >= self.params.expiry_cutoff_hour:
            return "next_day"
        return "same_day"

    # ------------------------------------------------------------------
    # PUBLIC: Position management
    # ------------------------------------------------------------------

    def should_cut_and_reenter(self, position: SpreadPosition, new_signal: Signal) -> bool:
        return position.direction != new_signal.direction

    def should_close(
        self,
        position: SpreadPosition,
        current_mark: float,
    ) -> Literal["profit_target", "stop_loss"] | None:
        # Take Profit and Stop Loss completely disabled
        return None

    def should_time_exit(self, position: SpreadPosition, now: datetime) -> bool:
        """Check if a pre-cutoff (same-day) position should be exited by time."""
        if position.expiry_type != "same_day":
            return False
        elapsed_min = (now - position.entry_time).total_seconds() / 60.0
        return elapsed_min >= self.params.early_exit_minutes

    def apply_cooldown(self, last_close_time: datetime, now: datetime) -> bool:
        return (now - last_close_time).total_seconds() < self.params.cooldown_seconds

    # ------------------------------------------------------------------
    # PRIVATE: Directional spread selection
    # ------------------------------------------------------------------

    def _select_directional_spread(
        self,
        signal: Signal,
        chain_by_expiry: dict[str, pd.DataFrame],
    ) -> SpreadCandidate | None:
        """
        Bull Put Spread (buy signal): Sell OTM put, buy further OTM put
          → Credit spread, profits if price stays above short strike
        Bear Put Spread (sell signal): Buy ATM/ITM put, sell further OTM put
          → Debit spread, profits if price drops
        """
        expiry_type = self.get_expiry_type(signal.timestamp)
        expiries = list(chain_by_expiry.keys())

        if expiry_type == "same_day":
            target_expiries = expiries[:1]  # First (today)
        else:
            target_expiries = expiries[1:2] if len(expiries) > 1 else expiries[:1]

        for idx, expiry in enumerate(target_expiries):
            chain = chain_by_expiry[expiry]
            label = "0dte" if idx == 0 and expiry_type == "same_day" else "next_day"
            candidate = self._build_directional_spread(signal, expiry, chain, label)
            if candidate is not None:
                return candidate
        return None

    def _build_directional_spread(
        self,
        signal: Signal,
        expiry: str,
        chain: pd.DataFrame,
        expiry_label: str,
    ) -> SpreadCandidate | None:
        if chain.empty:
            return None
        normalized = chain.copy()
        normalized["option_type"] = normalized["option_type"].str.lower()
        normalized = normalized[normalized["option_type"] == "put"].copy()
        if normalized.empty:
            return None

        normalized["strike"] = pd.to_numeric(normalized["strike"], errors="raise")
        normalized["mark"] = self._price_series(normalized)
        spot = self._spot(normalized)
        if spot is None:
            return None

        width = self.params.spread_width
        offset_pct = self.params.strike_offset_pct

        if signal.direction == "bull":
            # Bull Put Spread: sell put slightly OTM, buy put further OTM
            short_target = spot * (1.0 - offset_pct)
            short_candidates = normalized[normalized["strike"] <= spot].copy()
            if short_candidates.empty:
                return None
            short_candidates["dist"] = (short_candidates["strike"] - short_target).abs()
            short_candidates = short_candidates.sort_values("dist")

            for _, short in short_candidates.iterrows():
                long_strike = float(short["strike"]) - width
                long_matches = normalized[normalized["strike"] == long_strike]
                if long_matches.empty:
                    # Find nearest strike below
                    below = normalized[normalized["strike"] < float(short["strike"])]
                    if below.empty:
                        continue
                    below = below.copy()
                    below["dist"] = (below["strike"] - long_strike).abs()
                    long_matches = below.sort_values("dist").head(1)
                long = long_matches.iloc[0]
                raw_credit = float(short["mark"]) - float(long["mark"])
                net_credit = raw_credit * (1.0 - self.params.slippage_pct)
                if net_credit > 0:
                    return SpreadCandidate(
                        direction=signal.direction,
                        expiry=expiry,
                        expiry_label=expiry_label,
                        short_leg=self._leg(short, "sell", expiry, "put"),
                        long_leg=self._leg(long, "buy", expiry, "put"),
                        net_credit=net_credit,
                        width=abs(float(short["strike"]) - float(long["strike"])),
                    )

        else:
            # Bear Put Spread: buy put near ATM, sell put further OTM
            long_target = spot * (1.0 + offset_pct)
            long_candidates = normalized.copy()
            long_candidates["dist"] = (long_candidates["strike"] - long_target).abs()
            long_candidates = long_candidates.sort_values("dist")

            for _, long in long_candidates.iterrows():
                short_strike = float(long["strike"]) - width
                short_matches = normalized[normalized["strike"] == short_strike]
                if short_matches.empty:
                    below = normalized[normalized["strike"] < float(long["strike"])]
                    if below.empty:
                        continue
                    below = below.copy()
                    below["dist"] = (below["strike"] - short_strike).abs()
                    short_matches = below.sort_values("dist").head(1)
                short = short_matches.iloc[0]
                # Net debit (long mark > short mark for bear put spread)
                raw_credit = float(short["mark"]) - float(long["mark"])
                net_credit = raw_credit * (1.0 - self.params.slippage_pct) if raw_credit > 0 else raw_credit * (1.0 + self.params.slippage_pct)
                # For bear put spread, net_credit is negative (it's a debit)
                # We store the absolute value and track direction
                return SpreadCandidate(
                    direction=signal.direction,
                    expiry=expiry,
                    expiry_label=expiry_label,
                    short_leg=self._leg(short, "sell", expiry, "put"),
                    long_leg=self._leg(long, "buy", expiry, "put"),
                    net_credit=net_credit,  # Will be negative for bear put
                    width=abs(float(long["strike"]) - float(short["strike"])),
                )

        return None

    # ------------------------------------------------------------------
    # PRIVATE: Legacy credit-spread selection (kept for backward compat)
    # ------------------------------------------------------------------

    def _legacy_detect_crossover(self, candles: pd.DataFrame) -> Signal | None:
        """Original EMA crossover detection."""
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

    def _legacy_select_spread(
        self,
        signal: Signal,
        chain_by_expiry: dict[str, pd.DataFrame],
    ) -> SpreadCandidate | None:
        """Original credit-spread selection."""
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
                    expiry_label=expiry_label,
                    short_leg=self._leg(short, "sell", expiry, option_type),
                    long_leg=self._leg(long, "buy", expiry, option_type),
                    net_credit=net_credit,
                    width=abs(float(short["strike"]) - float(long["strike"])),
                )
        return None

    # ------------------------------------------------------------------
    # PRIVATE: Indicators
    # ------------------------------------------------------------------

    def _compute_rsi(self, close: pd.Series, period: int) -> pd.Series | None:
        """Compute RSI from close prices."""
        if len(close) < period + 1:
            return None
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("inf"))
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

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
            option_type=option_type,
            strike=float(row["strike"]),
            expiry=expiry,
            side=side,
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
