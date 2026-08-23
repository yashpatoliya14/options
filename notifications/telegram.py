"""
Telegram notifications, per spec section 16's exact message templates.
Never includes API keys/secrets in any message body.
"""
import requests
from config import StrategyConfig


class TelegramNotifier:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.enabled = cfg.telegram_enabled and bool(cfg.telegram_bot_token) and bool(cfg.telegram_chat_id)

    def _send(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
        try:
            requests.post(url, data={"chat_id": self.cfg.telegram_chat_id, "text": text}, timeout=10)
        except Exception:
            # Notification failures must never crash the trading loop.
            pass

    def signal(self, trend: str, underlying_price: float, supertrend_value: float, cfg: StrategyConfig) -> None:
        emoji = "🟢" if trend == "BUY" else "🔴"
        self._send(
            f"{emoji} SUPERTREND {trend}\n\n"
            f"Underlying: ${underlying_price:,.2f}\n"
            f"Supertrend: ${supertrend_value:,.2f}\n"
            f"Timeframe: {cfg.timeframe.upper()}\n"
            f"ATR: {cfg.supertrend_atr_period}\n"
            f"Multiplier: {cfg.supertrend_multiplier}"
        )

    def trade_entry(self, signal: str, option_type: str, strike: float, expiry: str,
                     premium: float, underlying_price: float, status: str = "ORDER PLACED") -> None:
        self._send(
            f"📈 OPTION SELL ENTRY\n\n"
            f"Signal: {signal}\n"
            f"Option: {option_type.upper()}\n"
            f"Strike: {strike:,.0f}\n"
            f"Expiry: {expiry}\n"
            f"Premium: ${premium:,.2f}\n"
            f"Underlying: ${underlying_price:,.2f}\n\n"
            f"Status: {status}"
        )

    def trade_exit(self, reason: str, entry_premium: float, exit_premium: float,
                    gross_pnl: float, fees: float, net_pnl: float) -> None:
        self._send(
            f"🔴 POSITION CLOSED\n\n"
            f"Reason: {reason}\n"
            f"Entry Premium: ${entry_premium:,.2f}\n"
            f"Exit Premium: ${exit_premium:,.2f}\n"
            f"Gross P&L: ${gross_pnl:,.2f}\n"
            f"Fees: ${fees:,.2f}\n"
            f"Net P&L: ${net_pnl:,.2f}"
        )

    def error(self, context: str, message: str) -> None:
        self._send(f"⚠️ ERROR\n\nContext: {context}\nMessage: {message}")
