"""
Risk management.

Simple fixed-lot sizing: every trade uses FIXED_LOT_SIZE lots.
If the account balance is insufficient for the required margin, the trade
is rejected. No margin-budget-based dynamic sizing.

All limits are configurable in config.py / .env.
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

        quantity = self.cfg.fixed_lot_size
        total_margin_needed = margin_per_lot * quantity

        # Reject if balance is insufficient for the required margin
        if total_margin_needed > self.cfg.margin_budget_usd:
            return SizingResult(
                0, margin_per_lot, 0.0,
                f"insufficient_balance_${self.cfg.margin_budget_usd:.2f}_need_${total_margin_needed:.2f}_for_{quantity}_lots",
            )

        return SizingResult(quantity, margin_per_lot, total_margin_needed)

    def daily_loss_limit_breached(self, realized_pnl_today: float) -> bool:
        if self.cfg.max_daily_loss_usd <= 0:
            return False
        return realized_pnl_today <= -abs(self.cfg.max_daily_loss_usd)
