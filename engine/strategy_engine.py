from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd

from .config_schema import StrategyParams
from .indicators import adx as adx_indicator
from .indicators import ema, realized_vol, resolution_seconds, rsi, supertrend, wilder_atr
from .models import Leg, Signal, SpreadCandidate, SpreadPosition
from .option_analytics import (
    clone_leg_qty,
    enrich_chain_greeks,
    expected_slippage_pct,
    net_spread_credit,
    passes_liquidity,
    qty_for_risk,
    score_spread,
    select_expiry_date,
    target_long_put_strike,
    target_short_put_strike,
)


class StrategyEngine:
    """
    Directional options strategy engine.

    Signal generation: Supertrend on closed candles, optional independent filters.
    Trade execution: Bull Put Spread for buy signals, Bear Put Spread for sell signals.
    """

    def __init__(self, params: StrategyParams):
        self.params = params

    def required_lookback(self) -> int:
        return self.params.required_lookback()

    # ------------------------------------------------------------------
    # PUBLIC: Signal detection
    # ------------------------------------------------------------------

    def detect_signal(self, candles: pd.DataFrame) -> Signal | None:
        """
        Supertrend signal on fully closed candles.

        A signal fires on the first bar where the Supertrend direction has held
        for min_trend_bars after a flip. Optional filters are applied only when
        enabled so they can be tested independently.
        """
        closed = self._closed_candles(candles)
        min_bars = max(
            self.params.supertrend_atr_period + 5,
            self.params.rsi_period + 2,
            self.params.trend_filter_period + 2 if self.params.trend_filter_enabled else 0,
            self.params.min_trend_bars + 2,
            self.params.adx_period * 2 + 2 if self.params.adx_filter_enabled else 0,
        )
        if len(closed) < min_bars:
            return None

        close = pd.to_numeric(closed["close"], errors="raise")
        high = pd.to_numeric(closed["high"], errors="raise")
        low = pd.to_numeric(closed["low"], errors="raise")

        st = supertrend(
            high,
            low,
            close,
            period=self.params.supertrend_atr_period,
            multiplier=self.params.supertrend_multiplier,
        )
        trend_series = st["trend"]
        if trend_series.isna().all() or len(trend_series) < 2:
            return None

        current = int(trend_series.iloc[-1])
        n_required = max(self.params.min_trend_bars, 1)
        if len(trend_series) < n_required + 1:
            return None

        for i in range(1, n_required + 1):
            if int(trend_series.iloc[-i]) != current:
                return None
        if int(trend_series.iloc[-(n_required + 1)]) == current:
            return None

        if current == 1:
            direction = "bull"
            signal_type = "buy"
        else:
            direction = "bear"
            signal_type = "sell"

        current_close = float(close.iloc[-1])
        ema_filter = ema(close, self.params.trend_filter_period)
        current_ema = float(ema_filter.iloc[-1])

        if self.params.trend_filter_enabled:
            if direction == "bull" and current_close < current_ema:
                return None
            if direction == "bear" and current_close > current_ema:
                return None

        rsi_series = rsi(close, self.params.rsi_period)
        rsi_value = float(rsi_series.iloc[-1]) if rsi_series is not None and len(rsi_series) else None
        if self.params.rsi_filter_enabled and rsi_value is not None:
            if direction == "bull" and rsi_value < self.params.rsi_oversold:
                return None
            if direction == "bear" and rsi_value > self.params.rsi_overbought:
                return None

        adx_value = self._latest_adx(closed)
        if self.params.adx_filter_enabled:
            if adx_value is None or adx_value < self.params.adx_trend_threshold:
                return None

        if self.params.session_filter_enabled and self.params.allowed_utc_hours.strip():
            allowed = {int(part.strip()) for part in self.params.allowed_utc_hours.split(",") if part.strip()}
            hour = int(self._to_datetime(closed["timestamp"].iloc[-1]).hour)
            if hour not in allowed:
                return None

        atr_pct = self._latest_atr_pct(closed)
        if self.params.atr_pct_filter_enabled:
            if atr_pct is None or not (self.params.min_atr_pct <= atr_pct <= self.params.max_atr_pct):
                return None

        periods = 365 * 24 * 3600 / max(resolution_seconds(self.params.resolution), 1)
        rv_series = realized_vol(close, self.params.rv_window, int(periods))
        rv_value = float(rv_series.iloc[-1]) if pd.notna(rv_series.iloc[-1]) else None
        if self.params.iv_rv_filter_enabled:
            rv_last = rv_value
            if rv_last is None or rv_last <= 0:
                return None
            if self.params.iv_mode == "realized_scaled":
                ratio = rv_last / max(self.params.assumed_iv, 1e-6)
            else:
                ratio = self.params.assumed_iv / rv_last
            if ratio < self.params.iv_rv_min_ratio:
                return None

        return Signal(
            timestamp=self._to_datetime(closed["timestamp"].iloc[-1]),
            direction=direction,
            fast_ema=current_close,
            slow_ema=current_ema,
            adx=adx_value,
            rsi=rsi_value,
            ema_trend="supertrend",
            signal_type=signal_type,
            atr_pct=atr_pct,
            rv=rv_value,
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
        if self.params.spread_type == "directional":
            return self._select_directional_spread(signal, chain_by_expiry)
        return self._legacy_select_spread(signal, chain_by_expiry)

    def get_expiry_type(self, signal_time: datetime) -> str:
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
        """
        Unified credit/debit exits.

        Mark is always short_put - long_put. Unrealized ≈ entry_credit - mark.
        Credit spreads profit when the net credit shrinks.
        Debit spreads profit when the net credit becomes more negative
        (long put outperforms short put).
        """
        entry = float(position.entry_credit)
        mark = float(current_mark)
        unrealized = entry - mark
        width = float(position.width)

        if entry > 0:
            if unrealized >= entry * self.params.take_profit_pct:
                return "profit_target"
            if mark >= entry * self.params.stop_loss_pct:
                return "stop_loss"
            return None

        debit = abs(entry)
        if debit <= 0:
            return None
        max_profit = max(width - debit, 0.0)
        if max_profit > 0 and unrealized >= max_profit * self.params.take_profit_pct:
            return "profit_target"
        if unrealized <= -debit * self.params.stop_loss_pct:
            return "stop_loss"
        return None

    def should_time_exit(self, position: SpreadPosition, now: datetime) -> bool:
        if position.expiry_type != "same_day":
            return False
        elapsed_min = (now - position.entry_time).total_seconds() / 60.0
        return elapsed_min >= self.params.early_exit_minutes

    def should_greek_exit(self, net_delta: float | None, dte: float | None) -> bool:
        if not self.params.greek_exit_enabled:
            return False
        if net_delta is not None and abs(net_delta) > self.params.max_abs_net_delta:
            return True
        if dte is not None and dte < self.params.min_dte_exit:
            return True
        return False

    def should_max_hold_exit(self, position: SpreadPosition, now: datetime) -> bool:
        """Global time-based exit in hours, independent of expiry type."""
        max_hours = float(self.params.max_hold_hours)
        if max_hours <= 0:
            return False
        elapsed = (now - position.entry_time).total_seconds() / 3600.0
        return elapsed >= max_hours

    def apply_cooldown(self, last_close_time: datetime, now: datetime) -> bool:
        return (now - last_close_time).total_seconds() < self.params.cooldown_seconds

    def size_candidate(self, candidate: SpreadCandidate, equity: float | None = None) -> SpreadCandidate:
        max_loss = candidate.width - abs(candidate.net_credit) if candidate.net_credit > 0 else abs(candidate.net_credit)
        qty = qty_for_risk(self.params, max_loss, equity)
        if qty <= 0:
            return candidate
        if qty == candidate.short_leg.qty:
            return candidate
        return SpreadCandidate(
            direction=candidate.direction,
            expiry=candidate.expiry,
            expiry_label=candidate.expiry_label,
            short_leg=clone_leg_qty(candidate.short_leg, qty),
            long_leg=clone_leg_qty(candidate.long_leg, qty),
            net_credit=candidate.net_credit,
            width=candidate.width,
        )

    # ------------------------------------------------------------------
    # PRIVATE: Directional spread selection
    # ------------------------------------------------------------------

    def _select_directional_spread(
        self,
        signal: Signal,
        chain_by_expiry: dict[str, pd.DataFrame],
    ) -> SpreadCandidate | None:
        expiry_type = self.get_expiry_type(signal.timestamp)
        expiries = list(chain_by_expiry.keys())
        mode = self.params.expiry_selection
        if mode in {"nearest_valid_after_signal", "cutoff_hour"}:
            target_expiries = select_expiry_date(expiries, signal.timestamp, self.params, expiry_type)
        else:
            target_expiries = select_expiry_date(expiries, signal.timestamp, self.params, expiry_type)

        best: SpreadCandidate | None = None
        best_score = float("-inf")
        for idx, expiry in enumerate(target_expiries):
            chain = enrich_chain_greeks(
                chain_by_expiry[expiry],
                signal.timestamp,
                expiry,
                self.params.risk_free_rate,
            )
            # expiry_type must reflect the ACTUAL selected expiry date, not the
            # signal hour: with DTE-scored expiry selection a pre-cutoff signal
            # can hold a next-day contract, and the 0DTE time-exit must not fire
            # for it.
            actual_type = (
                "same_day" if str(expiry)[:10] == signal.timestamp.date().isoformat() else "next_day"
            )
            label = "0dte" if actual_type == "same_day" else "next_day"
            candidate = self._build_directional_spread(signal, expiry, chain, label)
            if candidate is None:
                continue
            if not self.params.spread_scoring_enabled:
                return self.size_candidate(candidate)
            score = self._candidate_score(candidate, chain)
            if score > best_score:
                best_score = score
                best = candidate
        if best is not None:
            return self.size_candidate(best)
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
        option_type = "put" if signal.direction == "bull" else "call"
        normalized = normalized[normalized["option_type"] == option_type].copy()
        if normalized.empty:
            return None

        normalized["strike"] = pd.to_numeric(normalized["strike"], errors="raise")
        spot = self._spot(normalized)
        if spot is None:
            return None

        width = self.params.spread_width
        if signal.direction == "bull":
            short_target = spot * (1.0 - self.params.strike_offset_pct)
            short_candidates = normalized[normalized["strike"] <= spot].copy()
            long_filter = lambda strike, short_strike: strike < short_strike
            long_strike = lambda short_strike: short_strike - width
        else:
            if self.params.bear_structure == "put_debit":
                return self._build_bear_put_debit(signal, expiry, expiry_label, normalized, spot, width)
            short_target = spot * (1.0 + self.params.strike_offset_pct)
            short_candidates = normalized[normalized["strike"] >= spot].copy()
            long_filter = lambda strike, short_strike: strike > short_strike
            long_strike = lambda short_strike: short_strike + width
        if short_candidates.empty:
            return None
        short_candidates["dist"] = (short_candidates["strike"] - short_target).abs()
        short_candidates = short_candidates.sort_values("dist")

        ranked: list[tuple[float, SpreadCandidate]] = []
        for _, short in short_candidates.iterrows():
            if not passes_liquidity(short, self.params):
                continue
            target = long_strike(float(short["strike"]))
            long_candidates = normalized[normalized["strike"].map(lambda strike: long_filter(float(strike), float(short["strike"])))].copy()
            if long_candidates.empty:
                continue
            long_candidates["dist"] = (long_candidates["strike"] - target).abs()
            for _, long in long_candidates.sort_values("dist").iterrows():
                if not passes_liquidity(long, self.params):
                    continue
                raw_credit = net_spread_credit(short, long, self.params)
                if raw_credit <= 0 or expected_slippage_pct(short, long) > self.params.max_slippage_pct:
                    continue
                candidate = SpreadCandidate(
                    direction=signal.direction,
                    expiry=expiry,
                    expiry_label=expiry_label,
                    short_leg=self._leg(short, "sell", expiry, option_type),
                    long_leg=self._leg(long, "buy", expiry, option_type),
                    net_credit=raw_credit,
                    width=abs(float(short["strike"]) - float(long["strike"])),
                )
                ranked.append((score_spread(candidate, short, long, self.params), candidate))
                if not self.params.spread_scoring_enabled:
                    return candidate
                break

        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    def _build_bear_put_debit(
        self,
        signal: Signal,
        expiry: str,
        expiry_label: str,
        normalized: pd.DataFrame,
        spot: float,
        width: float,
    ) -> SpreadCandidate | None:
        """
        Bear Put Spread (debit): buy the higher-strike put, sell the lower-strike put.

        Long-put target is delta-aware when the chain carries greeks
        (|delta| ≈ target_long_delta); otherwise strike_offset_pct above spot.
        Entry credit is negative (debit); exits use the unified debit path.
        """
        long_target = target_long_put_strike(spot, normalized, self.params)
        candidates = normalized.copy()
        candidates["dist"] = (candidates["strike"] - long_target).abs()
        candidates = candidates.sort_values("dist")

        ranked: list[tuple[float, SpreadCandidate]] = []
        for _, long in candidates.iterrows():
            if not passes_liquidity(long, self.params):
                continue
            long_strike_val = float(long["strike"])
            short_strike_val = long_strike_val - width
            shorts = normalized[normalized["strike"] < long_strike_val].copy()
            if shorts.empty:
                continue
            shorts["dist"] = (shorts["strike"] - short_strike_val).abs()
            for _, short in shorts.sort_values("dist").iterrows():
                if not passes_liquidity(short, self.params):
                    continue
                if expected_slippage_pct(short, long) > self.params.max_slippage_pct:
                    continue
                raw_credit = net_spread_credit(short, long, self.params)  # negative = debit
                if raw_credit >= 0:
                    continue
                candidate = SpreadCandidate(
                    direction=signal.direction,
                    expiry=expiry,
                    expiry_label=expiry_label,
                    short_leg=self._leg(short, "sell", expiry, "put"),
                    long_leg=self._leg(long, "buy", expiry, "put"),
                    net_credit=raw_credit,
                    width=long_strike_val - float(short["strike"]),
                )
                ranked.append((score_spread(candidate, short, long, self.params), candidate))
                if not self.params.spread_scoring_enabled:
                    return candidate
                break

        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    def _candidate_score(self, candidate: SpreadCandidate, chain: pd.DataFrame) -> float:
        short = chain[chain["strike"] == candidate.short_leg.strike]
        long = chain[chain["strike"] == candidate.long_leg.strike]
        if short.empty or long.empty:
            return float("-inf")
        return score_spread(candidate, short.iloc[0], long.iloc[0], self.params)

    def _pair_long_put(
        self,
        signal: Signal,
        expiry: str,
        expiry_label: str,
        normalized: pd.DataFrame,
        short: pd.Series,
        width: float,
    ) -> SpreadCandidate | None:
        long_strike = float(short["strike"]) - width
        long_matches = normalized[normalized["strike"] == long_strike]
        if long_matches.empty:
            below = normalized[normalized["strike"] < float(short["strike"])]
            if below.empty:
                return None
            below = below.copy()
            below["dist"] = (below["strike"] - long_strike).abs()
            long_matches = below.sort_values("dist").head(1)
        long = long_matches.iloc[0]
        if not passes_liquidity(long, self.params):
            return None
        if expected_slippage_pct(short, long) > self.params.max_slippage_pct:
            return None
        net_credit = net_spread_credit(short, long, self.params)
        if net_credit <= 0:
            return None
        return SpreadCandidate(
            direction=signal.direction,
            expiry=expiry,
            expiry_label=expiry_label,
            short_leg=self._leg(short, "sell", expiry, "put"),
            long_leg=self._leg(long, "buy", expiry, "put"),
            net_credit=net_credit,
            width=abs(float(short["strike"]) - float(long["strike"])),
        )

    # ------------------------------------------------------------------
    # PRIVATE: Legacy credit-spread selection (kept for backward compat)
    # ------------------------------------------------------------------

    def _legacy_detect_crossover(self, candles: pd.DataFrame) -> Signal | None:
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

        adx_value = self._latest_adx(closed)
        if self.params.adx_min > 0:
            if adx_value is None or adx_value < self.params.adx_min:
                return None

        return Signal(
            timestamp=self._to_datetime(closed["timestamp"].iloc[-1]),
            direction=direction,
            fast_ema=now_fast,
            slow_ema=now_slow,
            adx=adx_value,
        )

    def _legacy_select_spread(
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
    # PRIVATE: Indicators / helpers
    # ------------------------------------------------------------------

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
        value = adx_indicator(high, low, close, period).iloc[-1]
        return None if pd.isna(value) else float(value)

    def _latest_atr_pct(self, candles: pd.DataFrame) -> float | None:
        period = max(self.params.supertrend_atr_period, 2)
        if len(candles) < period + 2:
            return None
        high = pd.to_numeric(candles["high"], errors="raise")
        low = pd.to_numeric(candles["low"], errors="raise")
        close = pd.to_numeric(candles["close"], errors="raise")
        atr_series = wilder_atr(high, low, close, period)
        value = atr_series.iloc[-1]
        last_close = float(close.iloc[-1])
        if pd.isna(value) or last_close <= 0:
            return None
        return float(value) / last_close

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
        if chain.empty or "strike" not in chain.columns:
            return None
        strikes = pd.to_numeric(chain["strike"], errors="coerce").dropna()
        if strikes.empty:
            return None
        return float(strikes.median())

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
