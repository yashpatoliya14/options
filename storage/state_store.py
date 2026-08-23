"""
Local SQLite persistence (confirmed: no MongoDB needed).

Tables mirror the spec's suggested collections (section 15), adapted to SQL:
  signals, trades, orders, positions, bot_state, errors

This is what makes live_algo.py restart-safe (spec section 14): on startup,
it reconciles `positions` here against the exchange's real open positions,
and never re-opens a position that's already live.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    trend TEXT NOT NULL,
    reference_price REAL NOT NULL,
    signal_change INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_timestamp INTEGER NOT NULL,
    exit_timestamp INTEGER,
    signal TEXT NOT NULL,
    option_type TEXT NOT NULL,
    strike REAL NOT NULL,
    expiry TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_premium REAL NOT NULL,
    exit_premium REAL,
    exit_reason TEXT,
    status TEXT NOT NULL DEFAULT 'open'  -- 'open' | 'closed'
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    order_id TEXT,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL,
    success INTEGER NOT NULL,
    message TEXT
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    context TEXT,
    message TEXT
);
"""


class StateStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- signals ----------
    def record_signal(self, timestamp: int, trend: str, reference_price: float, signal_change: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO signals (timestamp, trend, reference_price, signal_change) VALUES (?,?,?,?)",
                (timestamp, trend, reference_price, int(signal_change)),
            )

    # ---------- trades ----------
    def record_trade_entry(self, entry_timestamp: int, signal: str, option_type: str, strike: float,
                            expiry: str, quantity: int, entry_premium: float) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades (entry_timestamp, signal, option_type, strike, expiry,
                   quantity, entry_premium, status) VALUES (?,?,?,?,?,?,?, 'open')""",
                (entry_timestamp, signal, option_type, strike, expiry, quantity, entry_premium),
            )
            return cur.lastrowid

    def record_trade_exit(self, exit_timestamp: int, exit_premium: Optional[float], exit_reason: str) -> None:
        """Closes the most recent open trade."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE trades SET exit_timestamp=?, exit_premium=?, exit_reason=?, status='closed'
                   WHERE id = (SELECT id FROM trades WHERE status='open' ORDER BY id DESC LIMIT 1)""",
                (exit_timestamp, exit_premium, exit_reason),
            )

    def get_open_trade(self) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM trades WHERE status='open' ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def get_all_trades(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]

    # ---------- orders ----------
    def record_order(self, timestamp: int, order_id: Optional[str], side: str, symbol: str,
                      quantity: int, price: Optional[float], success: bool, message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO orders (timestamp, order_id, side, symbol, quantity, price, success, message)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (timestamp, order_id, side, symbol, quantity, price, int(success), message),
            )

    # ---------- bot_state (generic key/value, e.g. last processed candle timestamp) ----------
    def set_state(self, key: str, value: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO bot_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    # ---------- errors ----------
    def record_error(self, timestamp: int, context: str, message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO errors (timestamp, context, message) VALUES (?,?,?)",
                (timestamp, context, message),
            )
