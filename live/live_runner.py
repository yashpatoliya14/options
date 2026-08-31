from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from typing import Callable

from engine import Leg, SpreadCandidate, SpreadPosition, StrategyEngine, StrategyParams, TradeRecord
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
        self.lookback = lookback or max(params.ema_slow + params.adx_period + 5, 50)
        self.notify_fn = notify_fn
        self.position: SpreadPosition | None = self._load_position()
        self._last_close_time: datetime | None = None

    def run_forever(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.poll_seconds)

    def run_once(self) -> None:
        now = self.provider.clock.now()
        candles = self.provider.get_candles(
            self.params.candle_symbol,
            self.params.resolution,
            self.lookback,
        )
        if self.position is not None:
            close_reason = self.engine.should_close(
                self.position,
                self.executor.mark_to_market(self.position),
            )
            if close_reason is not None:
                self._close_position(now, close_reason)

        signal = self.engine.detect_crossover(candles)
        if signal is None:
            return

        if self.position is not None:
            if not self.engine.should_cut_and_reenter(self.position, signal):
                return
            self._close_position(signal.timestamp, "signal_cut")

        if self._last_close_time is not None and self.engine.apply_cooldown(
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
        self._open_position(candidate, signal.timestamp)

    def _open_position(self, candidate: SpreadCandidate, timestamp: datetime) -> None:
        fill = self.executor.open_spread(candidate.direction, candidate.short_leg, candidate.long_leg)
        if not fill.ok or fill.entry_or_exit_credit is None:
            return
        self.position = SpreadPosition.from_candidate(
            candidate,
            entry_time=timestamp,
            entry_credit=fill.entry_or_exit_credit,
        )
        self._save_position()
        if self.notify_fn:
            direction_label = f"{candidate.direction.upper()} PUT" if candidate.direction == "bull" else f"{candidate.direction.upper()} CALL"
            msg = (
                f"🟢 OPEN {direction_label} SPREAD\n"
                f"Underlying: {self.params.underlying}\n"
                f"Status: FILLED\n\n"
                f"Legs:\n"
                f"🔴 SELL {candidate.short_leg.qty}x {candidate.short_leg.symbol}\n"
                f"🟢 BUY {candidate.long_leg.qty}x {candidate.long_leg.symbol}\n\n"
                f"Trade Details:\n"
                f"Expiry: {candidate.expiry} ({candidate.expiry_label})\n"
                f"Spread Width: ${candidate.width:.2f}\n"
                f"Net Credit: ${fill.entry_or_exit_credit:.2f}\n"
                f"Commission: ${fill.commission:.2f}"
            )
            self.notify_fn(msg)

    def _close_position(self, timestamp: datetime, reason: ExitReason) -> None:
        if self.position is None:
            return
        position = self.position
        fill = self.executor.close_spread(position)
        if not fill.ok or fill.entry_or_exit_credit is None:
            return
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
            realized_pnl=(position.entry_credit - fill.entry_or_exit_credit) * position.qty - fill.commission,
            slippage=fill.slippage,
            commission=fill.commission,
            data_mode="live",
        )
        self._append_trade(trade)
        self.position = None
        self._last_close_time = timestamp
        self._save_position()
        
        if self.notify_fn:
            emoji = "✅" if trade.realized_pnl > 0 else "❌"
            direction_label = f"{trade.direction.upper()} PUT" if trade.direction == "bull" else f"{trade.direction.upper()} CALL"
            msg = (
                f"{emoji} CLOSE {direction_label} SPREAD\n"
                f"Status: FILLED\n"
                f"Reason: {reason}\n\n"
                f"Legs:\n"
                f"🟢 BUY {position.short_leg.qty}x {position.short_leg.symbol}\n"
                f"🔴 SELL {position.long_leg.qty}x {position.long_leg.symbol}\n\n"
                f"Trade Details:\n"
                f"Exit Credit: ${fill.entry_or_exit_credit:.2f}\n"
                f"Slippage: ${fill.slippage:.2f}\n"
                f"Commission: ${fill.commission:.2f}\n"
                f"Realized PnL: ${trade.realized_pnl:.2f}"
            )
            self.notify_fn(msg)

    def _save_position(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.position) if self.position is not None else None
        self.state_path.write_text(json.dumps({"position": payload}, default=str, indent=2), encoding="utf-8")

    def _load_position(self) -> SpreadPosition | None:
        if not self.state_path.exists():
            return None
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        payload = data.get("position")
        if not payload:
            return None
        return SpreadPosition(
            direction=payload["direction"],
            expiry=payload["expiry"],
            expiry_label=payload["expiry_label"],
            short_leg=Leg(**payload["short_leg"]),
            long_leg=Leg(**payload["long_leg"]),
            net_credit=float(payload["net_credit"]),
            width=float(payload["width"]),
            entry_time=datetime.fromisoformat(payload["entry_time"]),
            entry_credit=float(payload["entry_credit"]),
            qty=int(payload["qty"]),
            stop_order_id=payload.get("stop_order_id"),
        )

    def _append_trade(self, trade: TradeRecord) -> None:
        self.trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.trade_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(trade), default=str) + "\n")
