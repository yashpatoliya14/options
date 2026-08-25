"""
Delta Exchange India REST client.

Endpoints used (per official docs, docs.delta.exchange / India base URL
https://api.india.delta.exchange):
  GET  /v2/products                 -> contract specs, list of tradeable products
  GET  /v2/tickers                  -> live option chain: ?contract_types=call_options,put_options&underlying_asset_symbols=BTC&expiry_date=DD-MM-YYYY
  GET  /v2/history/candles          -> OHLCV candles: ?symbol=...&resolution=...&start=...&end=...
  GET  /v2/orders/margined          -> margin calculation (endpoint name to verify against current docs)
  POST /v2/orders                   -> place order
  DELETE /v2/orders/{id}            -> cancel order
  GET  /v2/positions/margined       -> current open positions

AUTH: Delta requires HMAC-SHA256 signed requests for private endpoints
(api-key, signature, timestamp headers). Implemented in _signed_headers().

NOTE: This client has been written against publicly documented endpoint
shapes but has NOT been exercised against a live account (no network access
in this build environment, and no API keys were provided). Before running
live_algo.py in live or paper mode:
  1. Run run_diagnostic.py with your real DELTA_API_KEY/SECRET to confirm
     these endpoint paths and response shapes still match Delta's current API
     (exchanges change endpoint names/params over time).
  2. Test everything against Delta's testnet first (USE_TESTNET=true).
"""
import hashlib
import hmac
import re
import time
from typing import Any, Dict, List, Optional

import requests

from broker import Broker, OptionQuote, OrderResult, Position
from config import StrategyConfig


class DeltaClient(Broker):
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.base_url = cfg.delta_base_url.rstrip("/")
        self.api_key = cfg.delta_api_key
        self.api_secret = cfg.delta_api_secret
        self.session = requests.Session()

    # ---------- auth ----------

    def _signature(self, method: str, path: str, query: str, body: str, timestamp: str) -> str:
        message = method + timestamp + path + query + body
        return hmac.new(
            self.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _signed_headers(self, method: str, path: str, query: str = "", body: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time()))
        sig = self._signature(method, path, query, body, timestamp)
        return {
            "Accept": "application/json",
            "api-key": self.api_key,
            "signature": sig,
            "timestamp": timestamp,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None, signed: bool = False) -> Any:
        url = self.base_url + path
        headers = {"Accept": "application/json"}
        if signed:
            query = ("?" + requests.compat.urlencode(params)) if params else ""
            headers = self._signed_headers("GET", path, query)
        resp = self.session.get(url, params=params, headers=headers, timeout=15)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            # Delta often puts the useful reason in its JSON response body.
            # Preserve the HTTP status while making the actionable API error
            # visible to the caller.
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:500]
            raise requests.HTTPError(f"{exc}; Delta response: {detail}", response=resp) from exc
        return resp.json()

    def _post(self, path: str, body: dict) -> Any:
        import json
        url = self.base_url + path
        body_str = json.dumps(body)
        headers = self._signed_headers("POST", path, "", body_str)
        resp = self.session.post(url, data=body_str, headers=headers, timeout=15)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:500]
            raise requests.HTTPError(f"{exc}; Delta response: {detail}", response=resp) from exc
        return resp.json()

    # ---------- market data ----------

    def get_historical_candles(self, symbol: str, resolution: str, start: int, end: int) -> List[dict]:
        """resolution examples: '1m','5m','15m','1h','3h' (verify '3h' is supported;
        docs list up to '1h'/'2h'/'4h'/'6h' -- if '3h' isn't a native resolution,
        aggregate from '1h' candles instead, 3 at a time, closing on the 3rd)."""
        data = self._get("/v2/history/candles", {
            "symbol": symbol, "resolution": resolution, "start": start, "end": end,
        })
        return data.get("result", [])

    def get_products(self) -> List[dict]:
        data = self._get("/v2/products")
        return data.get("result", [])

    # ---------- Broker interface ----------

    def get_available_expiries(self, underlying: str, as_of_timestamp: Optional[int] = None) -> List[str]:
        """
        Derive available expiries for `underlying` from /v2/products (filter to
        option contracts, extract expiry dates, sort ascending, return today's
        first). as_of_timestamp is ignored for live use (always "now").
        """
        products = self.get_products()
        expiries = sorted({
            p["settlement_time"][:10]  # verify actual field name in product schema
            for p in products
            if p.get("underlying_asset", {}).get("symbol") == underlying
            and p.get("contract_type") in ("call_options", "put_options")
            and p.get("settlement_time")
        })
        return expiries

    def get_option_chain(self, underlying: str, expiry: str, timestamp: Optional[int] = None) -> List[OptionQuote]:
        # Delta's tickers endpoint expects DD-MM-YYYY per documented example;
        # our internal convention is YYYY-MM-DD -- convert here.
        y, m, d = expiry.split("-")
        expiry_ddmmyyyy = f"{d}-{m}-{y}"
        data = self._get("/v2/tickers", {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying,
            "expiry_date": expiry_ddmmyyyy,
        })
        quotes = []
        for t in data.get("result", []):
            # field names (strike_price, best_bid, contract_type) per documented
            # ticker schema -- reconfirm against a live response before trusting.
            option_type = "call" if "call" in t.get("contract_type", "") else "put"
            best_bid = t.get("quotes", {}).get("best_bid") or t.get("mark_price")
            if best_bid is None:
                continue
            quotes.append(OptionQuote(
                symbol=t.get("symbol"),
                option_type=option_type,
                strike=float(t.get("strike_price", 0)),
                expiry=expiry,
                premium=float(best_bid),  # selling: use best_bid, the realistic price you could actually sell at
                underlying_price=float(t.get("spot_price", 0) or 0),
                timestamp=int(time.time()),
            ))
        return quotes

    def estimate_margin_per_lot(self, quote: OptionQuote) -> float:
        """
        Estimate margin requirement for short options on Delta Exchange.
        
        Based on typical option margin formulas and Delta's product specs:
        1. Initial margin is typically higher than maintenance margin
        2. For short options, margin = max(premium + risk component, minimum margin)
        3. Risk component is based on underlying price movement
        
        This is an improved estimate but still should be validated against
        Delta's actual margin calculator endpoint.
        """
        # Get notional value of one lot
        notional_per_lot = quote.strike * self.cfg.contract_value_underlying
        
        # For short options, typical margin formula includes:
        # 1. Premium received (you keep this, but need to cover potential loss)
        # 2. Risk component based on underlying price
        # 3. Minimum margin requirement
        
        # Risk component: for short options, typically 15-25% of notional
        # Risk component: higher for calls (unlimited upside), lower for puts
        risk_percentage = 0.20 if quote.option_type == "call" else 0.15
        
        # Premium collected for ONE lot (premium per BTC * lot size)
        premium_per_lot = quote.premium * self.cfg.contract_value_underlying

        # Calculate margin requirement
        risk_component = notional_per_lot * risk_percentage
        margin_required = premium_per_lot + risk_component
        
        # Minimum margin (floor) - at least premium + 10% of notional
        min_margin = premium_per_lot + (notional_per_lot * 0.10)
        
        return max(margin_required, min_margin)

    def place_sell_order(self, quote: OptionQuote, quantity: int) -> OrderResult:
        try:
            resp = self._post("/v2/orders", {
                "product_symbol": quote.symbol,
                "size": quantity,
                "side": "sell",
                "order_type": "market_order",  # confirm market vs limit choice with the user before going live
            })
            order = resp.get("result", {})
            return OrderResult(
                success=True,
                order_id=str(order.get("id")),
                filled_premium=float(order.get("average_fill_price", quote.premium)),
                quantity=quantity,
                message="order_placed",
            )
        except Exception as e:
            return OrderResult(False, None, None, quantity, f"order_failed: {e}")

    def close_position(self, position: Position) -> OrderResult:
        import datetime
        try:
            # Check if option has expired (Delta Exchange options expire at 12:00 PM UTC)
            expiry_dt = datetime.datetime.fromisoformat(position.expiry).replace(
                hour=12, minute=0, tzinfo=datetime.timezone.utc
            )
            if time.time() >= expiry_dt.timestamp():
                return OrderResult(
                    success=True, 
                    order_id="auto-settled", 
                    filled_premium=0.0, 
                    quantity=position.quantity, 
                    message="expired_settlement"
                )
        except Exception:
            pass

        try:
            resp = self._post("/v2/orders", {
                "product_symbol": position.symbol,
                "size": position.quantity,
                "side": "buy",  # buying back to close a short option
                "order_type": "market_order",
                "reduce_only": True,
            })
            order = resp.get("result", {})
            return OrderResult(
                success=True,
                order_id=str(order.get("id")),
                filled_premium=float(order.get("average_fill_price", 0)),
                quantity=position.quantity,
                message="position_closed",
            )
        except Exception as e:
            return OrderResult(False, None, None, position.quantity, f"close_failed: {e}")

    def get_open_positions(self) -> List[Position]:
        try:
            data = self._get("/v2/positions/margined", signed=True)
        except Exception:
            return []
        positions = []
        for p in data.get("result", []):
            size = float(p.get("size", 0))
            if size == 0:
                continue
            symbol = p.get("product_symbol", "")
            
            # Filter out non-option positions (like BTCUSD perpetuals)
            if not (symbol.startswith("C-") or symbol.startswith("P-")):
                continue
                
            option_type, strike, expiry = self._parse_option_symbol(symbol)
            if strike == 0.0:
                continue
                
            positions.append(Position(
                symbol=symbol,
                option_type=option_type,
                strike=strike,
                expiry=expiry,
                side="sell" if size < 0 else "buy",
                quantity=int(abs(size)),
                entry_premium=float(p.get("entry_price", 0)),
                entry_timestamp=0,
                strategy_direction="",  # unknown on reconnect -- live_algo.py must infer from option_type
            ))
        return positions

    @staticmethod
    def _parse_option_symbol(symbol: str):
        """Parse common Delta option symbols: C-BTC-70000-2026-08-23 or C-BTC-70000-250826."""
        # Match kind (C/P), underlying, strike, and date (either YYYY-MM-DD or DDMMYY or similar)
        match = re.match(r"^(?P<kind>[CP])-[^-]+-(?P<strike>[0-9]+(?:\.[0-9]+)?)-(?P<date>.+)$", symbol)
        if not match:
            return ("put" if symbol.startswith("P-") else "call", 0.0, "")
        
        date_str = match.group("date")
        # Try to normalize DDMMYY to YYYY-MM-DD if needed, or just return it as is.
        # live_algo expects YYYY-MM-DD for its own tracking but delta_client can use what it wants
        # if we just return date_str.
        if re.match(r"^\d{6}$", date_str):
            # DDMMYY -> 20YY-MM-DD (assuming 20xx)
            date_str = f"20{date_str[4:6]}-{date_str[2:4]}-{date_str[0:2]}"
            
        return (
            "put" if match.group("kind") == "P" else "call",
            float(match.group("strike")),
            date_str,
        )
