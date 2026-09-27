"""SQLite persistence for the live runner: the open position + a full trade log.

Why: the runner used to keep its position in memory only, so an accidental
restart started flat and would open a *second* trade on top of the one already
live on Delta. This module makes the open position durable — on startup the
runner reloads any still-open position from here (`load_open`) instead of
entering again, and every ENTRY / ROLL / TAKE-PROFIT is appended to `trade_log`.

Only *placed* positions are persisted (the runner guards writes on --place /
PLACE_ORDERS); a dry-run never writes real state. The DB file path is a fixed
constant (`STATE_DB` in config.py), not an env var. One open position at a time.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from config import STATE_DB


def _now_iso() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


class Store:
    def __init__(self, path=STATE_DB):
        self.path = str(path)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        c = self._conn()
        try:
            c.execute(
                """CREATE TABLE IF NOT EXISTS position (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       status TEXT NOT NULL DEFAULT 'open',
                       bias TEXT, side INTEGER, entry REAL,
                       sl_id INTEGER, fut_id INTEGER,
                       opt_symbol TEXT, opt_settle TEXT, opt_strike INTEGER,
                       opened_at TEXT, closed_at TEXT, close_reason TEXT)""")
            c.execute(
                """CREATE TABLE IF NOT EXISTS trade_log (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       ts TEXT NOT NULL, event TEXT NOT NULL,
                       bias TEXT, side INTEGER, symbol TEXT,
                       strike INTEGER, price REAL, detail TEXT)""")
            c.commit()
        finally:
            c.close()

    # --- open position (at most one 'open' row) ----------------------------
    def load_open(self) -> dict | None:
        c = self._conn()
        try:
            r = c.execute("SELECT * FROM position WHERE status='open' "
                          "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            c.close()
        if r is None:
            return None
        return {"_id": r["id"], "bias": r["bias"], "side": r["side"],
                "entry": r["entry"], "sl_id": r["sl_id"], "fut_id": r["fut_id"],
                "opt_symbol": r["opt_symbol"],
                "opt_settle": pd.Timestamp(r["opt_settle"]),
                "opt_strike": r["opt_strike"]}

    def save_open(self, pos: dict) -> int:
        c = self._conn()
        try:
            cur = c.execute(
                """INSERT INTO position
                       (status, bias, side, entry, sl_id, fut_id,
                        opt_symbol, opt_settle, opt_strike, opened_at)
                   VALUES ('open',?,?,?,?,?,?,?,?,?)""",
                (pos["bias"], int(pos["side"]), float(pos["entry"]),
                 pos.get("sl_id"), pos.get("fut_id"), pos["opt_symbol"],
                 pd.Timestamp(pos["opt_settle"]).isoformat(),
                 int(pos["opt_strike"]), _now_iso()))
            c.commit()
            pos["_id"] = cur.lastrowid
            return cur.lastrowid
        finally:
            c.close()

    def update_option(self, pos: dict) -> None:
        if pos.get("_id") is None:
            return
        c = self._conn()
        try:
            c.execute("UPDATE position SET opt_symbol=?, opt_settle=?, "
                      "opt_strike=? WHERE id=?",
                      (pos["opt_symbol"], pd.Timestamp(pos["opt_settle"]).isoformat(),
                       int(pos["opt_strike"]), pos["_id"]))
            c.commit()
        finally:
            c.close()

    def close_open(self, pos: dict, reason: str) -> None:
        c = self._conn()
        try:
            if pos.get("_id") is not None:
                c.execute("UPDATE position SET status='closed', closed_at=?, "
                          "close_reason=? WHERE id=?", (_now_iso(), reason, pos["_id"]))
            else:
                c.execute("UPDATE position SET status='closed', closed_at=?, "
                          "close_reason=? WHERE status='open'", (_now_iso(), reason))
            c.commit()
        finally:
            c.close()

    def log(self, event: str, *, bias=None, side=None, symbol=None,
            strike=None, price=None, detail=None) -> None:
        c = self._conn()
        try:
            c.execute(
                """INSERT INTO trade_log (ts,event,bias,side,symbol,strike,price,detail)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (_now_iso(), event, bias, None if side is None else int(side),
                 symbol, None if strike is None else int(strike),
                 None if price is None else float(price), detail))
            c.commit()
        finally:
            c.close()
