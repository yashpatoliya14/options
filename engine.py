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


def _timeframe_seconds(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("h"): return int(tf[:-1]) * 3600
    elif tf.endswith("m"): return int(tf[:-1]) * 60
    elif tf.endswith("d"): return int(tf[:-1]) * 86400
    return 86400


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
        
        # HTF tracking
        self.htf_supertrend = SupertrendCalculator(
            atr_period=cfg.supertrend_atr_period,
            multiplier=cfg.supertrend_multiplier,
        )
        self.htf_duration = _timeframe_seconds(getattr(cfg, 'htf_timeframe', '1d'))
        self._current_htf_candle: Optional[Candle] = None
        self._htf_trend_value: Optional[str] = None
        
        # Restored on startup by live_algo.py after reconciling with the exchange
        # (spec section 14: duplicate protection across restarts).
        self.current_position: Optional[Position] = existing_position
        self._last_trend: Optional[Trend] = None
        self._active_signal: Optional[str] = None
        self._flat_reason: Optional[str] = None
        self._manual_sl_threshold: Optional[float] = None
        self._lowest_premium_seen: Optional[float] = None
        
        if existing_position is not None:
            self._last_trend = (
                Trend.BULLISH
                if existing_position.strategy_direction == "BUY"
                else Trend.BEARISH
            )
            self._active_signal = existing_position.strategy_direction

    def _check_expirations(self, timestamp: int) -> None:
        """Drop positions that have passed their expiration time."""
        if self.current_position is None:
            return
        
        import datetime
        try:
            # NOTE: hour=12 UTC is an assumption about Delta's settlement time.
            # Confirm against Delta's actual API/docs if available.
            expiry_dt = datetime.datetime.fromisoformat(self.current_position.expiry).replace(
                hour=12, minute=0, tzinfo=datetime.timezone.utc
            )
            if timestamp >= expiry_dt.timestamp():
                self._close_current_position(timestamp, reason="expired_settlement")
        except Exception as e:
            self.on_event(EngineEvent(
                kind="error",
                timestamp=timestamp,
                payload={"reason": str(e), "context": "check_expirations"},
            ))

    def on_candle_close(self, candle: Candle, htf_trend: Optional[str] = None) -> Optional[SupertrendPoint]:
        """
        Feed exactly one CLOSED candle. Never call this with a forming/live
        candle -- the spec requires signals only on confirmed candle closes.
        """
        self._check_expirations(candle.timestamp)
        
        # Aggregate HTF Candle
        bucket_start = ((candle.timestamp - 1) // self.htf_duration) * self.htf_duration
        bucket_end = bucket_start + self.htf_duration
        
        if self._current_htf_candle is None or self._current_htf_candle.timestamp != bucket_end:
            if self._current_htf_candle is not None:
                htf_point = self.htf_supertrend.update(self._current_htf_candle)
                if htf_point:
                    self._htf_trend_value = htf_point.trend.value
            
            self._current_htf_candle = Candle(
                timestamp=bucket_end,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close
            )
        else:
            self._current_htf_candle.high = max(self._current_htf_candle.high, candle.high)
            self._current_htf_candle.low = min(self._current_htf_candle.low, candle.low)
            self._current_htf_candle.close = candle.close
            
        # Use provided htf_trend (from args) or our internally tracked one
        effective_htf = htf_trend if htf_trend is not None else self._htf_trend_value
        
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
                "supertrend_value": point.value,
                "htf_trend": effective_htf,
            },
        ))

        if not point.signal_change:
            # Same trend persists.
            if self.current_position is None and self._active_signal == point.trend.value:
                # If we hit an SL, do not re-enter the same trend.
                if self._flat_reason == "stop_loss":
                    return point
                # If we are flat because our option expired, or we just started up, attempt re-entry!
            elif self.current_position is not None:
                # Position is already open, hold it.
                return point

        self._active_signal = point.trend.value
        self._handle_signal_change(point, effective_htf)
        return point

    def evaluate_stop_loss(self, current_premium: float, timestamp: int) -> bool:
        """
        Evaluates the stop-loss on the current open position.
        Returns True if the position was closed due to SL, False otherwise.
        """
        if self.current_position is None:
            return False
            
        if self._lowest_premium_seen is None or current_premium < self._lowest_premium_seen:
            self._lowest_premium_seen = current_premium
            
        # Trailing breakeven: lock in theta gains
        if self.cfg.trailing_breakeven_pct > 0:
            target_drop = self.current_position.entry_premium * (1 - self.cfg.trailing_breakeven_pct / 100.0)
            if self._lowest_premium_seen <= target_drop:
                # We captured X% of the premium, trail stop to breakeven
                if self._manual_sl_threshold is None or self._manual_sl_threshold > self.current_position.entry_premium:
                    self._manual_sl_threshold = self.current_position.entry_premium
            
        if self.cfg.use_underlying_sl and self._manual_sl_threshold is None:
            # Only use underlying Supertrend reversals for SL, ignore premium spikes
            # (unless the trailing breakeven above activated manual_sl_threshold)
            sl_threshold = float('inf')
        elif self._manual_sl_threshold is not None:
            sl_threshold = self._manual_sl_threshold
        else:
            sl_threshold = self.current_position.entry_premium * 2.0
        
        # We sold the option, so if premium goes UP above the threshold, we lose and SL triggers.
        if current_premium >= sl_threshold:
            self._close_current_position(timestamp, reason="stop_loss")
            # Note: current_position is now None, but _active_signal remains, 
            # preventing re-entry until Supertrend flips.
            return True
            
        return False

    def finalize(self, timestamp: int, reason: str = "end_of_backtest") -> None:
        """
        Flush engine state at the end of a run.

        Backtests should close any open position at the final timestamp so the
        run reports realized PnL instead of silently leaving one trade open.
        """
        if self.current_position is None:
            return
        self._close_current_position(timestamp, reason=reason)

    def _handle_signal_change(self, point: SupertrendPoint, htf_trend: Optional[str] = None) -> None:
        # 1. Close existing position if one is open (position reversal, spec section 4)
        if self.current_position is not None:
            if not self._close_current_position(point.timestamp, reason="supertrend_reversal"):
                # Never open the opposite leg while the original short is still
                # live. This avoids doubling exposure after a failed close.
                return

        # 2. Attempt to open the new position for the new signal direction
        self._attempt_open(point, htf_trend)

    def _attempt_open(self, point: SupertrendPoint, htf_trend: Optional[str] = None) -> None:
        if htf_trend is not None and point.trend.value != htf_trend:
            self.on_event(EngineEvent(
                kind="trade_skipped",
                timestamp=point.timestamp,
                payload={"reason": f"htf_trend_mismatch (HTF:{htf_trend} != Signal:{point.trend.value})", "signal": point.trend.value},
            ))
            return
            
        option_type = option_type_for_signal(point.trend.value)

        result = select_expiry_and_strike(
            broker=self.broker,
            underlying=self.cfg.underlying_asset,
            supertrend_value=point.value,
            spot_price=point.reference_price,
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
        self._flat_reason = None  # We successfully entered a trade

        self.on_event(EngineEvent(
            kind="trade_entry",
            timestamp=point.timestamp,
            payload={
                "signal": point.trend.value,
                "symbol": result.quote.symbol,
                "option_type": result.quote.option_type,
                "strike": result.quote.strike,
                "expiry": result.quote.expiry,
                "expiry_used": result.expiry_used,
                "premium": self.current_position.entry_premium,
                "quantity": order.quantity,
                "underlying_price": point.reference_price,
                "margin_per_lot": sizing.estimated_margin_per_lot,
                "order_id": getattr(order, "order_id", None),
            },
        ))

    def _close_current_position(self, timestamp: int, reason: str) -> bool:
        pos = self.current_position
        if pos is None:
            return True
        order = self.broker.close_position(pos)
        if not order.success:
            if "no_position_for_reduce_only" in str(order.message):
                # The position was already closed on the exchange (e.g. manually)
                order.success = True
                order.filled_premium = 0.0
                order.order_id = "closed_manually"
                reason = "reconciled_missing_on_exchange"
            else:
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
                "order_id": getattr(order, "order_id", None),
            },
        ))
        self.current_position = None
        self._manual_sl_threshold = None
        self._lowest_premium_seen = None
        self._flat_reason = reason
        return True
