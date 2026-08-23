"""
Risk management.

Confirmed sizing rule: margin-budget cap. Given a selected option contract,
compute the max number of lots whose combined estimated margin fits within
MARGIN_BUDGET_USD (config.py), subject to MAX_OPEN_POSITIONS = 1 (only one
strategy position at a time, per spec section 4).

All limits are configurable in config.py / .env -- nothing here is hard-coded
beyond the algorithm itself.
"""
from dataclasses import dataclass
from typing import Optional

from broker import Broker, OptionQuote
from config import StrategyConfig


@dataclass
class SizingResult:
    quantity: int
    estimated_margin_per_lot: float
    estimated_total_margin: float
    rejected_reason: Optional[str] = None


class RiskManager:
    def __init__(self, cfg: StrategyConfig, broker: Broker):
        self.cfg = cfg
        self.broker = broker

    def size_position(self, quote: OptionQuote, current_open_positions: int) -> SizingResult:
        if current_open_positions >= self.cfg.max_open_positions:
            return SizingResult(0, 0.0, 0.0, "max_open_positions_reached")

        margin_per_lot = self.broker.estimate_margin_per_lot(quote)
        if margin_per_lot <= 0:
            return SizingResult(0, 0.0, 0.0, "invalid_margin_estimate")

        budget = self.cfg.margin_budget_usd
        if self.cfg.max_margin_usage_usd > 0:
            budget = min(budget, self.cfg.max_margin_usage_usd)

        quantity = int(budget // margin_per_lot)

        if self.cfg.max_position_size > 0:
            quantity = min(quantity, self.cfg.max_position_size)

        if quantity < 1:
            return SizingResult(
                0, margin_per_lot, 0.0,
                f"margin_budget_${budget:.2f}_insufficient_for_1_lot_(needs_${margin_per_lot:.2f})",
            )

        return SizingResult(quantity, margin_per_lot, margin_per_lot * quantity)

    def daily_loss_limit_breached(self, realized_pnl_today: float) -> bool:
        if self.cfg.max_daily_loss_usd <= 0:
            return False
        return realized_pnl_today <= -abs(self.cfg.max_daily_loss_usd)
