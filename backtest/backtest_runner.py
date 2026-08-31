from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from engine import SpreadCandidate, SpreadPosition, StrategyEngine, StrategyParams, TradeRecord
from engine.models import ExitReason


@dataclass(frozen=True)
class BacktestResult:
    trades: list[TradeRecord]
    skipped_no_valid_credit: int
    skipped_cooldown: int
    report: dict


class BacktestRunner:
    def __init__(
        self,
        engine: StrategyEngine,
        provider,
        executor,
        params: StrategyParams,
        lookback: int | None = None,
    ):
        self.engine = engine
        self.provider = provider
        self.executor = executor
        self.params = params
        self.lookback = lookback or max(params.ema_slow + params.adx_period + 5, 50)
        self.position: SpreadPosition | None = None
        self._entry_costs: dict[int, tuple[float, float]] = {}
        self._last_close_time: datetime | None = None
        self.trades: list[TradeRecord] = []
        self.skipped_no_valid_credit = 0
        self.skipped_cooldown = 0

    def run(self) -> BacktestResult:
        for i, row in enumerate(self.provider.candles.itertuples(index=False)):
            if i % 100 == 0:
                print(f"Processing candle {i}...")
            now = pd.Timestamp(row.timestamp).to_pydatetime()
            self.provider.clock.set(now)
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
                continue

            if self.position is not None:
                if not self.engine.should_cut_and_reenter(self.position, signal):
                    continue
                self._close_position(signal.timestamp, "signal_cut")

            if self._last_close_time is not None and self.engine.apply_cooldown(
                self._last_close_time,
                signal.timestamp,
            ):
                self.skipped_cooldown += 1
                continue

            expiries = self.provider.get_available_expiries(self.params.underlying)
            chains = {
                expiry: self.provider.get_option_chain(self.params.underlying, expiry)
                for expiry in expiries[:2]
            }
            candidate = self.engine.select_expiry_and_spread(signal, chains)
            if candidate is None:
                self.skipped_no_valid_credit += 1
                continue
            self._open_position(candidate, signal.timestamp)

        if self.position is not None:
            self.provider.clock.set(self.provider.candles.iloc[-1]["timestamp"].to_pydatetime())
            self._close_position(self.provider.clock.now(), "expired")

        return BacktestResult(
            trades=self.trades,
            skipped_no_valid_credit=self.skipped_no_valid_credit,
            skipped_cooldown=self.skipped_cooldown,
            report=self._report(),
        )

    def _open_position(self, candidate: SpreadCandidate, timestamp: datetime) -> None:
        fill = self.executor.open_spread(
            candidate.direction,
            candidate.short_leg,
            candidate.long_leg,
        )
        if not fill.ok or fill.entry_or_exit_credit is None:
            self.skipped_no_valid_credit += 1
            return
        self.position = SpreadPosition.from_candidate(
            candidate,
            entry_time=timestamp,
            entry_credit=fill.entry_or_exit_credit,
        )
        self.executor.open_position = self.position
        self._entry_costs[id(self.position)] = (fill.slippage, fill.commission)

    def _close_position(self, timestamp: datetime, reason: ExitReason) -> None:
        if self.position is None:
            return
        position = self.position
        fill = self.executor.close_spread(position)
        if not fill.ok or fill.entry_or_exit_credit is None:
            return
        entry_slippage, entry_commission = self._entry_costs.pop(id(position), (0.0, 0.0))
        total_slippage = entry_slippage + fill.slippage
        total_commission = entry_commission + fill.commission
        realized = (
            (position.entry_credit - fill.entry_or_exit_credit) * position.qty
            - total_commission
        )
        self.trades.append(
            TradeRecord(
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
                slippage=total_slippage,
                commission=total_commission,
                data_mode=self.params.option_data_mode,
            )
        )
        self.position = None
        self._last_close_time = timestamp

    def _report(self) -> dict:
        pnl = [trade.realized_pnl for trade in self.trades]
        wins = [value for value in pnl if value > 0]
        losses = [value for value in pnl if value < 0]
        equity = []
        running = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for value in pnl:
            running += value
            peak = max(peak, running)
            max_drawdown = min(max_drawdown, running - peak)
            equity.append(running)
        return {
            "trade_count": len(self.trades),
            "skip_count": self.skipped_no_valid_credit + self.skipped_cooldown,
            "skipped_no_valid_credit": self.skipped_no_valid_credit,
            "skipped_cooldown": self.skipped_cooldown,
            "total_pnl": sum(pnl),
            "win_rate": (len(wins) / len(pnl)) if pnl else 0.0,
            "max_drawdown": max_drawdown,
            "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
            "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
            "data_mode": self.params.option_data_mode,
            "equity_curve": equity,
        }
