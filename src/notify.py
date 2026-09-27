"""Best-effort Telegram notifications for live trade events.

Reads TELEGRAM_ENABLED / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from .env. Sending
is never fatal: if Telegram is disabled or the API call fails, the runner keeps
trading and just logs the problem.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Telegram:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        enabled = os.getenv("TELEGRAM_ENABLED", "false").strip().lower() == "true"
        self.enabled = enabled and bool(self.token and self.chat)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=10,
            )
        except Exception as e:                       # never let notifications break trading
            print(f"[telegram] send failed: {e}", flush=True)
