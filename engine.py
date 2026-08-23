"""
StrategyEngine -- the single brain shared by backtest.py and live_algo.py.

It has NO idea whether it's running against historical data or a live feed.
It only knows:
  - a Broker to query option chains / place orders / estimate margin
  - a RiskManager to size positions
  - a callback to report events (trade opened, trade closed, signal seen)

Feed it CLOSED candles one at a time via `on_candle_close()`. It runs
Supertrend, detects confirmed signal changes, and reacts:

  - No position open, signal appears        -> attempt to open
  - Position open, opposite signal appears  -> close existing, attempt to open opposite
  - Position open, same signal persists     -> do nothing (spec section 2/3: hold
                                                 until opposite signal, premium
                                                 changes are NOT an exit trigger)
  - Position open, same signal re-confirms  -> do nothing (no duplicate positions,
                                                 spec section 4)

backtest.py and live_algo.py differ ONLY in:
  - what Broker implementation they hand to this engine
  - where candles come from (a historical loop vs a live feed)
  - what they do with the event callbacks (write to trades.csv vs send Telegram)
"""
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from broker import Broker, OptionQuote, OrderResult, Position
from config import StrategyConfig
from strategy.supertrend import Candle, SupertrendCalculator, SupertrendPoint, Trend
from strategy.option_selector import option_type_for_signal
from strategy.expiry_selector import select_expiry_and_strike
from strategy.risk_manager import RiskManager


@dataclass
class EngineEvent:
    kind: str  # 'signal' | 'trade_entry' | 'trade_exit' | 'trade_skipped' | 'error'
    timestamp: int
    payload: dict = field(default_factory=dict)


class StrategyEngine:
    def __init__(
        self,
        cfg: StrategyConfig,
        broker: Broker,
        on_event: Callable[[EngineEvent], None],
        existing_position: Optional[Position] = None,
    ):
        self.cfg = cfg
        self.broker = broker
        self.on_event = on_event
        self.supertrend = SupertrendCalculator(
            atr_period=cfg.supertrend_atr_period,
            multiplier=cfg.supertrend_multiplier,
        )
        self.risk_manager = RiskManager(cfg, broker)
        # Restored on startup by live_algo.py after reconciling with the exchange
        # (spec section 14: duplicate protection across restarts).
        self.current_position: Optional[Position] = existing_position
        self._last_trend: Optional[Trend] = None
        if existing_position is not None:
            self._last_trend = (
                Trend.BULLISH
                if existing_position.strategy_direction == "BUY"
                else Trend.BEARISH
            )

    def on_candle_close(self, candle: Candle) -> Optional[SupertrendPoint]:
        """
        Feed exactly one CLOSED 3H candle. Never call this with a forming/live
        candle -- the spec requires signals only on confirmed candle closes.
        """
        point = self.supertrend.update(candle)
        if point is None:
            return None  # still warming up (fewer than atr_period candles seen)

        self.on_event(EngineEvent(
            kind="signal",
            timestamp=point.timestamp,
            payload={
                "trend": point.trend.value,
                "reference_price": point.reference_price,
                "signal_change": point.signal_change,
            },
        ))

        if not point.signal_change:
            # Same trend persists -- explicitly do nothing, per spec.
            return point

        self._handle_signal_change(point)
        return point

    def finalize(self, timestamp: int, reason: str = "end_of_backtest") -> None:
        """
        Flush engine state at the end of a run.

        Backtests should close any open position at the final timestamp so the
        run reports realized PnL instead of silently leaving one trade open.
        """
        if self.current_position is None:
            return
        self._close_current_position(timestamp, reason=reason)

    def _handle_signal_change(self, point: SupertrendPoint) -> None:
        # 1. Close existing position if one is open (position reversal, spec section 4)
        if self.current_position is not None:
            if not self._close_current_position(point.timestamp, reason="supertrend_reversal"):
                # Never open the opposite leg while the original short is still
                # live. This avoids doubling exposure after a failed close.
                return

        # 2. Attempt to open the new position for the new signal direction
        self._attempt_open(point)

    def _attempt_open(self, point: SupertrendPoint) -> None:
        option_type = option_type_for_signal(point.trend.value)

        result = select_expiry_and_strike(
            broker=self.broker,
            underlying=self.cfg.underlying_asset,
            reference_price=point.reference_price,
            option_type=option_type,
            min_premium=self.cfg.min_premium_usd,
            as_of_timestamp=point.timestamp,
        )

        if result.quote is None:
            self.on_event(EngineEvent(
                kind="trade_skipped",
                timestamp=point.timestamp,
                payload={
                    "reason": result.rejected_reason,
                    "signal": point.trend.value,
                    "best_premium_seen": result.best_premium_seen,
                    "best_expiry_seen": result.best_expiry_seen,
                },
            ))
            return

        sizing = self.risk_manager.size_position(result.quote, current_open_positions=0)
        if sizing.quantity < 1:
            self.on_event(EngineEvent(
                kind="trade_skipped",
                timestamp=point.timestamp,
                payload={"reason": sizing.rejected_reason, "signal": point.trend.value},
            ))
            return

        order = self.broker.place_sell_order(result.quote, sizing.quantity)
        if not order.success:
            self.on_event(EngineEvent(
                kind="error",
                timestamp=point.timestamp,
                payload={"reason": order.message, "context": "place_sell_order"},
            ))
            return

        self.current_position = Position(
            symbol=result.quote.symbol,
            option_type=result.quote.option_type,
            strike=result.quote.strike,
            expiry=result.quote.expiry,
            side="sell",
            quantity=order.quantity,
            entry_premium=order.filled_premium if order.filled_premium is not None else result.quote.premium,
            entry_timestamp=point.timestamp,
            strategy_direction=point.trend.value,
        )
        self._last_trend = point.trend

        self.on_event(EngineEvent(
            kind="trade_entry",
            timestamp=point.timestamp,
            payload={
                "signal": point.trend.value,
                "option_type": result.quote.option_type,
                "strike": result.quote.strike,
                "expiry": result.quote.expiry,
                "expiry_used": result.expiry_used,
                "premium": self.current_position.entry_premium,
                "quantity": order.quantity,
                "underlying_price": point.reference_price,
                "margin_per_lot": sizing.estimated_margin_per_lot,
            },
        ))

    def _close_current_position(self, timestamp: int, reason: str) -> bool:
        pos = self.current_position
        if pos is None:
            return True
        order = self.broker.close_position(pos)
        if not order.success:
            self.on_event(EngineEvent(
                kind="error",
                timestamp=timestamp,
                payload={"reason": order.message, "context": "close_position"},
            ))
            return False
        exit_premium = order.filled_premium if order.filled_premium is not None else None

        self.on_event(EngineEvent(
            kind="trade_exit",
            timestamp=timestamp,
            payload={
                "reason": reason,
                "symbol": pos.symbol,
                "option_type": pos.option_type,
                "strike": pos.strike,
                "expiry": pos.expiry,
                "quantity": pos.quantity,
                "entry_premium": pos.entry_premium,
                "exit_premium": exit_premium,
                "entry_timestamp": pos.entry_timestamp,
            },
        ))
        self.current_position = None
        return True
