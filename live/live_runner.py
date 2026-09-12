from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from typing import Callable

from engine import Leg, SignalGate, SpreadCandidate, SpreadPosition, StrategyEngine, StrategyParams, TradeRecord
from engine.models import ExitReason


class LiveRunner:
    def __init__(
        self,
        engine: StrategyEngine,
        provider,
        executor,
        params: StrategyParams,
        state_path: str,
        trade_log_path: str,
        poll_seconds: int = 30,
        lookback: int | None = None,
        notify_fn: Callable[[str], None] | None = None,
    ):
        self.engine = engine
        self.provider = provider
        self.executor = executor
        self.params = params
        self.state_path = Path(state_path)
        self.trade_log_path = Path(trade_log_path)
        self.poll_seconds = poll_seconds
        # Lookback must cover Supertrend ATR + trend filter EMA + margin
        self.lookback = lookback or max(
            params.supertrend_atr_period + 10,
            params.trend_filter_period + 10 if params.trend_filter_enabled else 0,
            params.ema_slow + params.adx_period + 5,
            100,
        )
        self.notify_fn = notify_fn
        self.position: SpreadPosition | None = self._load_position()
        self._last_close_time: datetime | None = self._load_last_close_time()
        self.gate = SignalGate(params)

        # Safety: on startup, verify the exchange stop is still present if
        # use_exchange_stop is enabled.  A missing stop means one leg may have
        # already closed — repair before any new decisions.
        if self.position is not None and self.position.stop_order_id and self.params.use_exchange_stop:
            try:
                stopped = self.executor.client.get_order(self.position.stop_order_id)
                if not stopped or stopped.get("state") in ("filled", "cancelled", "rejected"):
                    print(f"[WARN] Startup orphan repair: stop {self.position.stop_order_id} "
                          f"is {stopped.get('state') if stopped else 'missing'}")
                    repair = self.executor.close_spread(self.position)
                    if repair.ok:
                        self.position = None
                        print("[WARN] Orphaned position force-closed on startup")
                    else:
                        print(f"[WARN] Orphan repair failed: {repair.message} — manual review needed")
            except Exception as exc:
                print(f"[WARN] Could not verify exchange stop at startup: {exc}")

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception as e:
                print(f"[ERROR] run_once failed: {e}")
                if self.notify_fn:
                    self.notify_fn(f"⚠️ <b>Error in run_once:</b>\n{str(e)[:500]}")
            time.sleep(self.poll_seconds)

    def run_once(self) -> None:
        now = self.provider.clock.now()
        candles = self.provider.get_candles(
            self.params.candle_symbol,
            self.params.resolution,
            self.lookback,
        )
        if self.position is not None:
            # --- Stop watchdog: if use_exchange_stop is on and the stop is gone,
            # one leg may have been filled by the stop — force-close the rest ---
            if (
                self.params.use_exchange_stop
                and self.position.stop_order_id is not None
            ):
                try:
                    stop_state = self.executor.client.get_order(self.position.stop_order_id)
                    if not stop_state or stop_state.get("state") in ("filled", "cancelled", "rejected"):
                        print(f"[WARN] Watchdog: stop {self.position.stop_order_id} "
                              f"is {stop_state.get('state') if stop_state else 'missing'} — "
                              "force-closing position")
                        self._close_position(now, "stop_loss")
                        now = self.provider.clock.now()
                except Exception as exc:
                    print(f"[WARN] Watchdog check failed: {exc}")

            # Check TP/SL exit
            close_reason = self.engine.should_close(
                self.position,
                self.executor.mark_to_market(self.position),
            )
            if close_reason is not None:
                self._close_position(now, close_reason)
            # Check time-based exit
            elif hasattr(self.engine, "should_time_exit") and self.engine.should_time_exit(self.position, now):
                self._close_position(now, "time_exit")

        signal = self.gate.consume(
            self.engine.detect_crossover(candles),
            now,
            last_close=float(candles["close"].iloc[-1]) if not candles.empty else None,
        )
        if signal is None:
            return

        # Handle existing position
        signal_cut = False
        if self.position is not None:
            if self.params.exit_on_opposite_signal:
                if not self.engine.should_cut_and_reenter(self.position, signal):
                    return
                self._close_position(signal.timestamp, "signal_cut")
                signal_cut = True
            else:
                # Position is open and signal_cut is disabled — skip this signal
                return

        if not signal_cut and self._last_close_time is not None and self.engine.apply_cooldown(
            self._last_close_time,
            signal.timestamp,
        ):
            return

        expiries = self.provider.get_available_expiries(self.params.underlying)
        chains = {
            expiry: self.provider.get_option_chain(self.params.underlying, expiry)
            for expiry in expiries[:2]
        }
        candidate = self.engine.select_expiry_and_spread(signal, chains)
        if candidate is None:
            return
        self._open_position(candidate, signal.timestamp, signal)

    def _open_position(self, candidate: SpreadCandidate, timestamp: datetime, signal=None) -> None:
        fill = self.executor.open_spread(candidate.direction, candidate.short_leg, candidate.long_leg)
        if not fill.ok or fill.entry_or_exit_credit is None:
            return

        signal_type = signal.signal_type if signal and getattr(signal, "signal_type", None) else candidate.direction
        # expiry_type reflects the ACTUAL contract being held (see backtest
        # runner for the same fix) so 0DTE time-exits only fire on same-day
        # contracts.
        if hasattr(self.engine, "get_expiry_type"):
            expiry_type = (
                "same_day"
                if pd.Timestamp(candidate.expiry).tz_localize(None).date() == pd.Timestamp(timestamp).tz_localize(None).date()
                else "next_day"
            )
        else:
            expiry_type = "unknown"

        self.position = SpreadPosition.from_candidate(
            candidate,
            entry_time=timestamp,
            entry_credit=fill.entry_or_exit_credit,
            stop_order_id=fill.stop_order_id or self._stop_id_from_fill(fill),
            signal_type=signal_type,
            expiry_type=expiry_type,
        )
        object.__setattr__(self.position, "entry_mid", (fill.short_mid - fill.long_mid) if fill.short_mid is not None and fill.long_mid is not None else None)
        object.__setattr__(self.position, "detection_time", self.provider.clock.now())
        self._save_position()

        if self.notify_fn:
            spread_type = "BULL PUT" if candidate.direction == "bull" else "BEAR CALL"
            credit_debit = f"Credit: ${fill.entry_or_exit_credit:.2f}" if fill.entry_or_exit_credit > 0 else f"Debit: ${abs(fill.entry_or_exit_credit):.2f}"
            msg = (
                f"🟢 <b>OPEN {spread_type} SPREAD</b>\n"
                f"<b>Underlying:</b> {self.params.underlying}\n"
                f"<b>Signal:</b> {'BUY' if candidate.direction == 'bull' else 'SELL'}\n\n"
                f"<b>Legs:</b>\n"
                f"🔴 SELL {candidate.short_leg.qty}x {candidate.short_leg.symbol}\n"
                f"🟢 BUY {candidate.long_leg.qty}x {candidate.long_leg.symbol}\n\n"
                f"<b>Trade Details:</b>\n"
                f"Expiry: {candidate.expiry} ({candidate.expiry_label})\n"
                f"Width: ${candidate.width:.0f}\n"
                f"{credit_debit}\n"
                f"Commission: ${fill.commission:.2f}"
            )
            self.notify_fn(msg)

    def _close_position(self, timestamp: datetime, reason: ExitReason) -> None:
        if self.position is None:
            return
        position = self.position
        fill = self.executor.close_spread(position)
        if not fill.ok or fill.entry_or_exit_credit is None:
            # Partial or full failure — position stays open for retry on next poll
            msg = (f"⚠️ <b>CLOSE FAILED — position still open</b>\n"
                   f"<b>Reason attempted:</b> {reason}\n"
                   f"<b>Error:</b> {fill.message}\n"
                   f"<b>Failed legs:</b> {', '.join(l.symbol for l in fill.legs)}\n\n"
                   f"Retrying on next poll cycle...")
            if self.notify_fn:
                self.notify_fn(msg)
            else:
                print(f"[WARN] {msg}")
            return

        cs = self.params.contract_size
        realized = (
            (position.entry_credit - fill.entry_or_exit_credit) * position.qty * cs
            - fill.commission
        )
        duration_min = (timestamp - position.entry_time).total_seconds() / 60.0

        dte_at_entry = None
        try:
            from engine.option_analytics import dte_days

            dte_at_entry = dte_days(position.expiry, position.entry_time)
        except Exception:
            pass

        trade = TradeRecord(
            entry_time=position.entry_time,
            exit_time=timestamp,
            direction=position.direction,
            expiry=position.expiry,
            expiry_label=position.expiry_label,
            short_strike=position.short_leg.strike,
            long_strike=position.long_leg.strike,
            credit_received=position.entry_credit,
            exit_reason=reason,
            realized_pnl=realized,
            slippage=fill.slippage,
            commission=fill.commission,
            data_mode="live",
            underlying=self.params.underlying,
            signal_type=getattr(position, "signal_type", position.direction),
            expiry_type=getattr(position, "expiry_type", "unknown"),
            trade_duration_minutes=duration_min,
            fees=fill.commission,
            signal_time=position.entry_time,
            detection_time=getattr(position, "detection_time", None),
            order_submit_time=fill.submitted_at,
            order_ack_time=fill.acked_at,
            fill_time=fill.filled_at,
            signal_to_fill_seconds=(
                (fill.filled_at - position.entry_time).total_seconds()
                if fill.filled_at is not None
                else None
            ),
            short_entry_bid=fill.short_bid,
            short_entry_ask=fill.short_ask,
            long_entry_bid=fill.long_bid,
            long_entry_ask=fill.long_ask,
            short_fill_price=fill.short_fill,
            long_fill_price=fill.long_fill,
            net_spread_entry=position.entry_credit,
            theoretical_mid_entry=position.entry_mid,
            backtest_theoretical_entry=position.entry_mid,
            live_actual_entry=position.entry_credit,
            entry_decay=(
                (position.entry_credit - position.entry_mid)
                if position.entry_mid is not None and position.entry_mid != 0.0
                else None
            ),
            dte_at_entry=dte_at_entry,
            utc_hour=position.entry_time.hour,
        )
        self._append_trade(trade)
        self.position = None
        self._last_close_time = timestamp
        self._save_position()

        if self.notify_fn:
            emoji = "✅" if trade.realized_pnl > 0 else "❌"
            spread_type = "BULL PUT" if trade.direction == "bull" else "BEAR CALL"
            msg = (
                f"{emoji} <b>CLOSE {spread_type} SPREAD</b>\n"
                f"<b>Reason:</b> {reason}\n"
                f"<b>Duration:</b> {duration_min:.0f} min\n\n"
                f"<b>Legs:</b>\n"
                f"🟢 BUY {position.short_leg.qty}x {position.short_leg.symbol}\n"
                f"🔴 SELL {position.long_leg.qty}x {position.long_leg.symbol}\n\n"
                f"<b>Result:</b>\n"
                f"Entry Credit: ${position.entry_credit:.2f}\n"
                f"Exit Cost: ${fill.entry_or_exit_credit:.2f}\n"
                f"Realized PnL: <b>${trade.realized_pnl:+.2f}</b>"
            )
            self.notify_fn(msg)

    def _save_position(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.position) if self.position is not None else None
        state = {
            "position": payload,
            "last_close_time": self._last_close_time.isoformat() if self._last_close_time else None,
        }
        self.state_path.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")

    @staticmethod
    def _stop_id_from_fill(fill) -> str | None:
        try:
            return json.loads(fill.message).get("stop_order_id")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _load_last_close_time(self) -> datetime | None:
        if not self.state_path.exists():
            return None
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8")).get("last_close_time")
            return datetime.fromisoformat(value) if value else None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _load_position(self) -> SpreadPosition | None:
        if not self.state_path.exists():
            return None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            payload = data.get("position")
            if not payload:
                return None
            position = SpreadPosition(
                direction=payload["direction"],
                expiry=payload["expiry"],
                expiry_label=payload["expiry_label"],
                short_leg=Leg(**payload["short_leg"]),
                long_leg=Leg(**payload["long_leg"]),
                net_credit=float(payload["net_credit"]),
                width=float(payload["width"]),
                entry_time=datetime.fromisoformat(payload["entry_time"]),
                entry_credit=float(payload["entry_credit"]),
                entry_spot=float(payload.get("entry_spot", 0.0)),
                qty=int(payload["qty"]),
                stop_order_id=payload.get("stop_order_id"),
                signal_type=payload.get("signal_type"),
                expiry_type=payload.get("expiry_type"),
            )
            object.__setattr__(position, "entry_mid", payload.get("entry_mid"))
            object.__setattr__(
                position,
                "detection_time",
                datetime.fromisoformat(payload["detection_time"]) if payload.get("detection_time") else None,
            )
            return position
        except Exception as e:
            print(f"[WARN] Failed to load position: {e}")
            return None

    def _append_trade(self, trade: TradeRecord) -> None:
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trade_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(trade), default=str) + "\n")
