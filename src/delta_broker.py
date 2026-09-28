"""Minimal Delta Exchange (India) REST client for placing the start-up orders.

Reads credentials from .env (DELTA_API_KEY / DELTA_API_SECRET). Honors
USE_TESTNET so live money is only ever touched when you explicitly set it false.
Only what the start-up entry needs: resolve products, place market orders.

Delta auth: signature = HMAC_SHA256(secret, method + timestamp + path + query + body).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class DeltaBroker:
    def __init__(self):
        self.key = os.getenv("DELTA_API_KEY", "").strip()
        self.secret = os.getenv("DELTA_API_SECRET", "").strip()
        self.testnet = os.getenv("USE_TESTNET", "true").strip().lower() == "true"
        if self.testnet:
            self.base = os.getenv("DELTA_TESTNET_URL", "https://cdn-ind.testnet.deltaex.org").strip()
        else:
            self.base = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange").strip()
        self.session = requests.Session()
        self._clock_offset: int | None = None   # server_time - local_time (seconds)

    # --- signing -----------------------------------------------------------
    def _sign(self, method: str, path: str, query: str, body: str) -> dict:
        # local clock + offset learned from the server (see _signed retry)
        ts = str(int(time.time()) + (self._clock_offset or 0))
        message = method + ts + path + query + body
        sig = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        return {
            "api-key": self.key,
            "signature": sig,
            "timestamp": ts,
            "Content-Type": "application/json",
            "User-Agent": "options-algo-startup",
        }

    def _send(self, method: str, path: str, payload: str) -> requests.Response:
        headers = self._sign(method, path, "", payload)
        return self.session.request(method, self.base + path,
                                    headers=headers, data=payload, timeout=30)

    def _signed(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = json.dumps(body, separators=(",", ":")) if body else ""
        r = self._send(method, path, payload)

        # The CDN clock (and the local clock) can drift enough that Delta rejects
        # the signature as expired. The error tells us the true server_time — sync
        # our offset to it and retry once. This is more reliable than the cacheable
        # HTTP Date header, which the CDN may serve stale.
        if r.status_code == 401 and "expired_signature" in r.text:
            try:
                ctx = r.json()["error"]["context"]
                self._clock_offset = int(ctx["server_time"]) - int(time.time())
            except (KeyError, ValueError):
                pass
            else:
                r = self._send(method, path, payload)

        if not r.ok:
            # surface Delta's error payload instead of a bare "400 Client Error"
            raise RuntimeError(f"{method} {path} -> HTTP {r.status_code}: {r.text}")
        return r.json()

    # --- public reads ------------------------------------------------------
    def _products(self, contract_types: str, live_only: bool = True) -> list:
        params = {"contract_types": contract_types}
        if live_only:
            params["states"] = "live"
        r = self.session.get(self.base + "/v2/products", params=params, timeout=30)
        r.raise_for_status()
        return r.json().get("result", [])

    def perpetual(self, symbol: str = "BTCUSD") -> dict:
        for p in self._products("perpetual_futures"):
            if p.get("symbol") == symbol:
                return p
        raise RuntimeError(f"Perpetual {symbol} not found on {self.base}")

    @staticmethod
    def _future_chain(chain: list, asset: str, ctype: str, base: str) -> list:
        """Options for `asset` whose settlement is strictly in the future.

        A daily 0DTE contract stays listed as "live" for a while after it settles
        (12:00 UTC), so filtering on settlement_time > now is what stops a roll
        from re-selecting the contract that just expired (which would make the
        runner roll — and, in --place mode, SELL — every loop). See the roll path.
        """
        now = pd.Timestamp.now(tz="UTC")
        out = []
        for p in chain:
            if p.get("underlying_asset", {}).get("symbol") != asset:
                continue
            if pd.Timestamp(p["settlement_time"]).tz_convert("UTC") <= now:
                continue                      # already settled — never select it
            out.append(p)
        if not out:
            raise RuntimeError(f"No unexpired {asset} {ctype} on {base}")
        return out

    def atm_option(self, spot: float, opt_type: str, asset: str = "BTC") -> dict:
        """Nearest *future* expiry option whose strike is closest to spot. 'C'/'P'."""
        ctype = "call_options" if opt_type == "C" else "put_options"
        chain = self._future_chain(self._products(ctype), asset, ctype, self.base)
        nearest_exp = min(p["settlement_time"] for p in chain)
        front = [p for p in chain if p["settlement_time"] == nearest_exp]
        return min(front, key=lambda p: abs(float(p["strike_price"]) - spot))

    def option_by_strike(self, strike: float, opt_type: str, asset: str = "BTC") -> dict:
        """Nearest *future* expiry option at (or closest to) a specific strike.
        Used to roll into a new short option struck at the future's entry price."""
        ctype = "call_options" if opt_type == "C" else "put_options"
        chain = self._future_chain(self._products(ctype), asset, ctype, self.base)
        nearest_exp = min(p["settlement_time"] for p in chain)
        front = [p for p in chain if p["settlement_time"] == nearest_exp]
        return min(front, key=lambda p: abs(float(p["strike_price"]) - strike))


    # --- orders ------------------------------------------------------------
    def place_market_order(self, product_id: int, size: int, side: str) -> dict:
        body = {"product_id": int(product_id), "size": int(size),
                "side": side, "order_type": "market_order"}
        return self._signed("POST", "/v2/orders", body)

    def place_stop_market_order(self, product_id: int, size: int, side: str,
                                stop_price: float, trigger: str = "mark_price") -> dict:
        """A stop-loss that fires a market order when price crosses `stop_price`.

        `side` is the closing side of the position it protects: 'sell' to stop a
        long, 'buy' to stop a short. Delta wants the trigger price as a string.
        """
        body = {"product_id": int(product_id), "size": int(size), "side": side,
                "order_type": "market_order", "stop_order_type": "stop_loss_order",
                "stop_price": str(stop_price), "stop_trigger_method": trigger}
        return self._signed("POST", "/v2/orders", body)

    def cancel_order(self, product_id: int, order_id: int) -> dict:
        """Cancel a resting order (e.g. the SuperTrend stop when we close early)."""
        body = {"id": int(order_id), "product_id": int(product_id)}
        return self._signed("DELETE", "/v2/orders", body)
