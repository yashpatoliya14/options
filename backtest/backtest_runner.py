from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from engine import SignalGate, SpreadCandidate, SpreadPosition, StrategyEngine, StrategyParams, TradeRecord
from engine.models import ExitReason, Signal
from engine.option_analytics import drawdown_risk_multiplier


@dataclass(frozen=True)
class BacktestResult:
    trades: list[TradeRecord]
    skipped_no_valid_credit: int
    skipped_cooldown: int
    report: dict


class BacktestRunner:
    """
    Candle-loop backtest sharing the exact live decision path.

    Execution realism:
      - per-leg bid/ask fills with slippage and notional fees (SimulatedExecutor)
      - execution_delay_seconds shifts the fill clock (fills may see the next
        candle's open when the delay crosses a bar boundary)
      - signals are de-duplicated via the shared SignalGate
      - expiries that have already passed are never selected
    """

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
        self.lookback = lookback or params.required_lookback()
        self.gate = SignalGate(params)
        self.position: SpreadPosition | None = None
        self._entry_meta: dict[int, dict] = {}
        self._last_close_time: datetime | None = None
        self.trades: list[TradeRecord] = []
        self.skipped_no_valid_credit = 0
        self.skipped_cooldown = 0
        self.equity = float(params.capital)
        self.peak_equity = float(params.capital)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> BacktestResult:
        total = len(self.provider.candles)
        for i, row in enumerate(self.provider.candles.itertuples(index=False)):
            if i % 500 == 0:
                print(f"  Processing candle {i}/{total}...")
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
                elif self.engine.should_max_hold_exit(self.position, now):
                    self._close_position(now, "time_exit")
                elif hasattr(self.engine, "should_time_exit") and self.engine.should_time_exit(self.position, now):
                    self._close_position(now, "time_exit")

            raw_signal = self.engine.detect_crossover(candles)
            signal = self.gate.consume(
                raw_signal,
                now,
                last_close=float(candles["close"].iloc[-1]) if candles is not None and not candles.empty else None,
            )
            if signal is None:
                continue

            signal_cut = False
            if self.position is not None:
                if self.params.exit_on_opposite_signal:
                    if not self.engine.should_cut_and_reenter(self.position, signal):
                        continue
                    self._close_position(signal.timestamp, "signal_cut")
                    signal_cut = True
                else:
                    # Position open and signal_cut disabled — skip this signal
                    continue

            if not signal_cut and self._last_close_time is not None and self.engine.apply_cooldown(
                self._last_close_time,
                signal.timestamp,
            ):
                self.skipped_cooldown += 1
                continue

            detection_time = self.provider.clock.now()
            expiries = self._valid_expiries()
            chains = {
                expiry: self.provider.get_option_chain(self.params.underlying, expiry)
                for expiry in expiries[:2]
            }
            chain_time = self.provider.clock.now()
            candidate = self.engine.select_expiry_and_spread(signal, chains)
            select_time = self.provider.clock.now()
            if candidate is None:
                self.skipped_no_valid_credit += 1
                continue
            self._open_position(
                candidate,
                signal.timestamp,
                signal,
                detection_time=detection_time,
                chain_time=chain_time,
                select_time=select_time,
                signal_cut=signal_cut,
            )

        if self.position is not None:
            self.provider.clock.set(self.provider.candles.iloc[-1]["timestamp"].to_pydatetime())
            self._close_position(self.provider.clock.now(), "expired")

        return BacktestResult(
            trades=self.trades,
            skipped_no_valid_credit=self.skipped_no_valid_credit,
            skipped_cooldown=self.skipped_cooldown,
            report=self._report(),
        )

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    def _valid_expiries(self) -> list[str]:
        """Expiries still tradeable at the current clock time.

        The provider already filters by ACTUAL settlement time (12:30 UTC);
        do NOT re-filter with raw date-midnight timestamps, which wrongly
        drops the same-day expiry for signals between 00:00 and 12:30 UTC
        (the settlement at 12:30 has not passed yet)."""
        return self.provider.get_available_expiries(self.params.underlying)

    def _sized_candidate(self, candidate: SpreadCandidate) -> SpreadCandidate | None:
        """Apply fixed-risk / drawdown-aware sizing. Returns None if qty drops to 0."""
        if self.params.sizing_mode == "fixed_qty":
            return candidate
        cs = self.params.contract_size
        if candidate.net_credit > 0:
            max_loss_per_contract = (candidate.width - candidate.net_credit) * cs
        else:
            max_loss_per_contract = abs(candidate.net_credit) * cs
        dd_frac = 0.0
        if self.params.dd_risk_reduction and self.peak_equity > 0:
            dd_frac = max(0.0, (self.peak_equity - self.equity) / self.peak_equity)
        mult = drawdown_risk_multiplier(self.params, dd_frac)
        effective = self.params.overlay({"risk_pct": self.params.risk_pct * mult})

        from engine.option_analytics import clone_leg_qty, qty_for_risk

        qty = qty_for_risk(effective, max_loss_per_contract, self.equity)
        if qty <= 0:
            return None
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

    def _open_position(
        self,
        candidate: SpreadCandidate,
        timestamp: datetime,
        signal: Signal | None = None,
        detection_time: datetime | None = None,
        chain_time: datetime | None = None,
        select_time: datetime | None = None,
        signal_cut: bool = False,
    ) -> None:
        sized = self._sized_candidate(candidate)
        if sized is None:
            self.skipped_no_valid_credit += 1
            return

        # Advance the clock by the execution delay so fills see the price the
        # market actually trades at after detection (next bar open if the delay
        # crosses a bar boundary). No signal logic uses this clock.
        delay = max(self.params.execution_delay_seconds, 0)
        submit_time = self.provider.clock.now()
        if delay > 0:
            self.provider.clock.set(submit_time + timedelta(seconds=delay))
        fill = self.executor.open_spread(sized.direction, sized.short_leg, sized.long_leg)
        fill_time = self.provider.clock.now()
        if delay > 0:
            # Restore the decision clock for subsequent loop iterations.
            self.provider.clock.set(submit_time)

        if not fill.ok or fill.entry_or_exit_credit is None:
            self.skipped_no_valid_credit += 1
            return

        signal_type = signal.signal_type if signal and getattr(signal, "signal_type", None) else sized.direction
        # expiry_type reflects the ACTUAL contract being held, not the signal
        # hour: with DTE-scored expiry selection a pre-cutoff signal can hold a
        # next-day expiry, and the 0DTE time-exit must not fire for it.
        if hasattr(self.engine, "get_expiry_type"):
            expiry_type = (
                "same_day"
                if pd.Timestamp(sized.expiry).tz_localize(None).date() == pd.Timestamp(timestamp).tz_localize(None).date()
                else "next_day"
            )
        else:
            expiry_type = "unknown"

        self.position = SpreadPosition.from_candidate(
            sized,
            entry_time=timestamp,
            entry_credit=fill.entry_or_exit_credit,
            signal_type=signal_type,
            expiry_type=expiry_type,
        )
        self.executor.open_position = self.position

        # Entry context for the trade record.
        spot = self._fill_spot()
        entry_mid_short, entry_mid_long, greeks = self._entry_chain_context(sized, timestamp)
        cs = self.params.contract_size
        self._entry_meta[id(self.position)] = {
            "slippage": fill.slippage,
            "commission": fill.commission,
            "spot": spot,
            "entry_mid_short": entry_mid_short,
            "entry_mid_long": entry_mid_long,
            "greeks": greeks,
            "detection_time": detection_time or timestamp,
            "chain_time": chain_time or timestamp,
            "select_time": select_time or timestamp,
            "submit_time": submit_time,
            "fill_time": fill_time,
            "signal_cut": signal_cut,
            "signal": signal,
        }

    def _fill_spot(self) -> float:
        try:
            return float(self.provider._spot_as_of(self.provider.clock.now()))
        except Exception:
            return 0.0

    def _entry_chain_context(self, candidate: SpreadCandidate, at_time: datetime) -> tuple[float, float, dict]:
        """Mid prices and greeks for both legs at the decision clock."""
        try:
            chain = self.provider.get_option_chain(self.params.underlying, candidate.expiry)
            short_rows = chain[chain["strike"] == candidate.short_leg.strike]
            long_rows = chain[chain["strike"] == candidate.long_leg.strike]
            short_row = short_rows.iloc[0] if not short_rows.empty else None
            long_row = long_rows.iloc[0] if not long_rows.empty else None
            mid_short = float(short_row["mark"]) if short_row is not None else 0.0
            mid_long = float(long_row["mark"]) if long_row is not None else 0.0
            greeks = {}
            for name, row in (("short", short_row), ("long", long_row)):
                if row is None:
                    continue
                for key in ("delta", "gamma", "theta", "vega", "iv"):
                    greeks[f"{name}_{key}"] = float(row[key]) if key in row and pd.notna(row[key]) else None
            return mid_short, mid_long, greeks
        except Exception:
            return 0.0, 0.0, {}

    def _exit_chain_context(self, position: SpreadPosition) -> tuple[float, float, float]:
        """Mid prices + spot at exit."""
        spot = self._fill_spot()
        try:
            chain = self.provider.get_option_chain(self.params.underlying, position.expiry)
            short_rows = chain[chain["strike"] == position.short_leg.strike]
            long_rows = chain[chain["strike"] == position.long_leg.strike]
            mid_short = float(short_rows.iloc[0]["mark"]) if not short_rows.empty else 0.0
            mid_long = float(long_rows.iloc[0]["mark"]) if not long_rows.empty else 0.0
            return mid_short, mid_long, spot
        except Exception:
            return 0.0, 0.0, spot

    def _close_position(self, timestamp: datetime, reason: ExitReason) -> None:
        if self.position is None:
            return
        position = self.position
        fill = self.executor.close_spread(position)
        if not fill.ok or fill.entry_or_exit_credit is None:
            return
        meta = self._entry_meta.pop(id(position), {})
        cs = self.params.contract_size
        qty = position.qty

        realized = (
            (position.entry_credit - fill.entry_or_exit_credit) * qty * cs
            - fill.commission
        )
        self.equity += realized
        self.peak_equity = max(self.peak_equity, self.equity)
        duration = (timestamp - position.entry_time).total_seconds() / 60.0

        width = position.width
        entry_credit = position.entry_credit
        if entry_credit > 0:
            max_profit = entry_credit
            max_loss = width - entry_credit
        else:
            max_profit = width - abs(entry_credit)
            max_loss = abs(entry_credit)

        # --- P&L attribution -------------------------------------------------
        greeks = meta.get("greeks", {})
        spot_entry = float(meta.get("spot", 0.0) or 0.0)
        spot_exit = self._fill_spot()
        mid_short_entry = float(meta.get("entry_mid_short", 0.0) or 0.0)
        mid_long_entry = float(meta.get("entry_mid_long", 0.0) or 0.0)
        mid_short_exit, mid_long_exit, _ = self._exit_chain_context(position)

        net_delta = None
        net_theta = None
        net_vega = None
        net_gamma = None
        sd, ld = greeks.get("short_delta"), greeks.get("long_delta")
        if sd is not None and ld is not None:
            # Position delta: short put contributes -(put delta); long put contributes +put delta.
            net_delta = (-sd) + ld
        st, lt = greeks.get("short_theta"), greeks.get("long_theta")
        if st is not None and lt is not None:
            net_theta = (-st) + lt
        sv, lv = greeks.get("short_vega"), greeks.get("long_vega")
        if sv is not None and lv is not None:
            net_vega = (-sv) + lv
        sg, lg = greeks.get("short_gamma"), greeks.get("long_gamma")
        if sg is not None and lg is not None:
            net_gamma = (-sg) + lg

        pnl_execution = None
        if mid_short_entry and mid_long_entry and fill.short_mid is not None and fill.long_mid is not None:
            entry_exec = (position.entry_credit)  # per contract
            entry_mid_net = mid_short_entry - mid_long_entry
            exit_mid_net = mid_short_exit - mid_long_exit
            exit_exec = fill.entry_or_exit_credit
            pnl_execution = ((entry_mid_net - entry_exec) + (exit_exec - exit_mid_net)) * qty * cs

        pnl_delta = pnl_theta = pnl_vega = None
        pnl_gamma = None
        if spot_entry > 0 and spot_exit > 0 and net_delta is not None:
            pnl_delta = (spot_exit - spot_entry) * net_delta * qty * cs
        if net_theta is not None:
            dt_days = max(duration, 0.0) / (60.0 * 24.0)
            pnl_theta = net_theta * dt_days * qty * cs
        iv_entry = greeks.get("short_iv")
        iv_exit = self._exit_iv(position)
        if net_vega is not None and iv_entry is not None and iv_exit is not None:
            pnl_vega = net_vega * (iv_exit - iv_entry) * 100.0 * qty * cs
        known = sum(v for v in (pnl_delta, pnl_theta, pnl_vega, pnl_execution) if v is not None)
        pnl_gamma = realized - known if known != 0 else None

        entry_mid_net = (mid_short_entry - mid_long_entry) if mid_short_entry and mid_long_entry else None

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
                slippage=meta.get("slippage", 0.0) + fill.slippage,
                commission=meta.get("commission", 0.0) + fill.commission,
                data_mode=self.params.option_data_mode,
                underlying=self.params.underlying,
                signal_type=getattr(position, "signal_type", position.direction),
                expiry_type=getattr(position, "expiry_type", "unknown"),
                trade_duration_minutes=duration,
                underlying_price=spot_exit,
                short_premium=mid_short_entry,
                long_premium=mid_long_entry,
                max_profit=max_profit * cs * qty,
                max_loss=max_loss * cs * qty,
                fees=meta.get("commission", 0.0) + fill.commission,
                entry_slippage=meta.get("slippage", 0.0),
                signal_time=position.entry_time,
                detection_time=meta.get("detection_time"),
                option_chain_time=meta.get("chain_time"),
                option_select_time=meta.get("select_time"),
                order_submit_time=meta.get("submit_time"),
                order_ack_time=meta.get("submit_time"),
                fill_time=meta.get("fill_time"),
                signal_to_fill_seconds=(
                    (meta.get("fill_time") - position.entry_time).total_seconds()
                    if meta.get("fill_time") is not None
                    else None
                ),
                short_entry_bid=fill.short_bid,
                short_entry_ask=fill.short_ask,
                long_entry_bid=fill.long_bid,
                long_entry_ask=fill.long_ask,
                short_fill_price=fill.short_fill,
                long_fill_price=fill.long_fill,
                net_spread_entry=position.entry_credit,
                theoretical_mid_entry=entry_mid_net,
                iv=greeks.get("short_iv"),
                realized_vol=meta.get("signal").rv if meta.get("signal") is not None else None,
                adx=meta.get("signal").adx if meta.get("signal") is not None else None,
                rsi=meta.get("signal").rsi if meta.get("signal") is not None else None,
                atr_pct=meta.get("signal").atr_pct if meta.get("signal") is not None else None,
                utc_hour=position.entry_time.hour,
                short_delta=greeks.get("short_delta"),
                long_delta=greeks.get("long_delta"),
                net_delta=net_delta,
                net_theta=net_theta,
                net_vega=net_vega,
                net_gamma=net_gamma,
                dte_at_entry=(
                    (pd.Timestamp(position.expiry, tz="UTC") - pd.Timestamp(position.entry_time)).total_seconds() / 86400.0
                ),
                pnl_delta=pnl_delta,
                pnl_theta=pnl_theta,
                pnl_vega=pnl_vega,
                pnl_gamma=pnl_gamma,
                pnl_execution=pnl_execution,
            )
        )
        self.position = None
        self._last_close_time = timestamp

    def _exit_iv(self, position: SpreadPosition) -> float | None:
        try:
            chain = self.provider.get_option_chain(self.params.underlying, position.expiry)
            rows = chain[chain["strike"] == position.short_leg.strike]
            if rows.empty:
                return None
            iv = rows.iloc[0].get("iv")
            return float(iv) if iv is not None and pd.notna(iv) else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _report(self) -> dict:
        pnl = [trade.realized_pnl for trade in self.trades]
        wins = [v for v in pnl if v > 0]
        losses = [v for v in pnl if v < 0]

        duration_days = 0.0
        if not self.provider.candles.empty:
            start_time = pd.Timestamp(self.provider.candles.iloc[0]["timestamp"])
            end_time = pd.Timestamp(self.provider.candles.iloc[-1]["timestamp"])
            duration_days = (end_time - start_time).total_seconds() / (24 * 3600)

        duration_years = duration_days / 365.25 if duration_days > 0 else 1.0

        trades_per_day = len(self.trades) / duration_days if duration_days > 0 else 0.0
        trades_per_month = trades_per_day * 30.44
        trades_per_year = trades_per_day * 365.25

        starting_capital = self.params.capital
        total_pnl = sum(pnl)
        ending_capital = self.equity
        total_return_pct = (total_pnl / starting_capital) * 100 if starting_capital > 0 else 0.0

        # Equity curve and drawdown
        equity = []
        running = starting_capital
        peak = running
        max_dd_dollar = 0.0
        max_dd_pct = 0.0
        for v in pnl:
            running += v
            equity.append(running)
            if running > peak:
                peak = running
            dd = running - peak
            if dd < max_dd_dollar:
                max_dd_dollar = dd
            dd_pct = (dd / peak * 100) if peak > 0 else 0.0
            if dd_pct < max_dd_pct:
                max_dd_pct = dd_pct

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        max_consec_losses = 0
        current_consec = 0
        for v in pnl:
            if v < 0:
                current_consec += 1
                max_consec_losses = max(max_consec_losses, current_consec)
            else:
                current_consec = 0

        durations = [t.trade_duration_minutes for t in self.trades]
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        total_fees = sum(t.fees for t in self.trades)
        total_slippage = sum(t.slippage for t in self.trades)

        sharpe = 0.0
        if len(pnl) > 1:
            mean_pnl = sum(pnl) / len(pnl)
            std_pnl = (sum((v - mean_pnl) ** 2 for v in pnl) / (len(pnl) - 1)) ** 0.5
            if std_pnl > 0:
                sharpe = (mean_pnl / std_pnl) * math.sqrt(max(trades_per_year, 1))

        sortino = 0.0
        if len(pnl) > 1:
            mean_pnl = sum(pnl) / len(pnl)
            downside = [min(v, 0) ** 2 for v in pnl]
            downside_dev = (sum(downside) / (len(pnl) - 1)) ** 0.5
            if downside_dev > 0:
                sortino = (mean_pnl / downside_dev) * math.sqrt(max(trades_per_year, 1))

        cagr = 0.0
        if starting_capital > 0 and ending_capital > 0 and duration_years > 0:
            cagr = ((ending_capital / starting_capital) ** (1.0 / duration_years) - 1.0) * 100

        bull_trades = [t for t in self.trades if t.signal_type == "buy"]
        bear_trades = [t for t in self.trades if t.signal_type == "sell"]

        def _sub_report(trades_list):
            if not trades_list:
                return {"trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "profit_factor": 0.0, "avg_pnl": 0.0}
            pnls = [t.realized_pnl for t in trades_list]
            w = [v for v in pnls if v > 0]
            l = [v for v in pnls if v < 0]
            gp = sum(w) if w else 0.0
            gl = abs(sum(l)) if l else 0.0
            pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
            return {
                "trades": len(trades_list),
                "win_rate": len(w) / len(pnls) if pnls else 0.0,
                "net_pnl": sum(pnls),
                "profit_factor": pf,
                "avg_pnl": sum(pnls) / len(pnls),
            }

        # Expectancy (per trade, after all costs)
        n = len(pnl)
        win_prob = len(wins) / n if n else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        expectancy = (win_prob * avg_win) - ((1.0 - win_prob) * avg_loss) if n else 0.0

        return {
            "trade_count": len(self.trades),
            "skip_count": self.skipped_no_valid_credit + self.skipped_cooldown,
            "skipped_no_valid_credit": self.skipped_no_valid_credit,
            "skipped_cooldown": self.skipped_cooldown,
            "starting_capital": starting_capital,
            "ending_capital": ending_capital,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": (len(wins) / n) if n else 0.0,
            "profit_factor": profit_factor,
            "avg_trade_pnl": (total_pnl / n) if n else 0.0,
            "avg_win": avg_win,
            "avg_loss": -avg_loss,
            "expectancy": expectancy,
            "max_drawdown_dollar": max_dd_dollar,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_consecutive_losses": max_consec_losses,
            "avg_trade_duration_min": avg_duration,
            "total_fees": total_fees,
            "total_slippage": total_slippage,
            "cagr": cagr,
            "avg_trades_per_day": trades_per_day,
            "avg_trades_per_month": trades_per_month,
            "avg_trades_per_year": trades_per_year,
            "data_mode": self.params.option_data_mode,
            "equity_curve": equity,
            "bull_put_spread": _sub_report(bull_trades),
            "bear_put_spread": _sub_report(bear_trades),
            # Legacy keys for backward compat
            "max_drawdown": max_dd_dollar,
        }
