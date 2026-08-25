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

    def _send(self, text: str, parse_mode: str = None, reply_markup: dict = None) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
        payload = {"chat_id": self.cfg.telegram_chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            # Notification failures must never crash the trading loop.
            pass
            
    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
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

    def status(self, message: str, parse_mode: str = None, reply_markup: dict = None) -> None:
        self._send(f"🤖 BOT UPDATE\n\n{message}", parse_mode=parse_mode, reply_markup=reply_markup)

    def get_updates(self) -> list:
        """Fetch latest telegram messages (without advancing offset)."""
        if not self.enabled:
            return []
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/getUpdates"
        try:
            r = requests.get(url, params={"allowed_updates": '["message", "callback_query"]'}, timeout=10)
            res = r.json()
            if res and res.get("ok"):
                return res.get("result", [])
        except Exception:
            pass
        return []
