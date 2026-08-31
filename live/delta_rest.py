from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import requests


class DeltaRestClient:
    def __init__(
        self,
        base_url: str = "https://cdn-ind.testnet.deltaex.org",
        api_key: str = "",
        api_secret: str = "",
        timeout_seconds: int = 15,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.session = requests.Session()

    def get_historical_candles(self, symbol: str, resolution: str, start: int, end: int) -> list[dict]:
        data = self._get(
            "/v2/history/candles",
            {"symbol": symbol, "resolution": resolution, "start": start, "end": end},
        )
        return data.get("result", [])

    def get_products(self) -> list[dict]:
        data = self._get("/v2/products")
        return data.get("result", [])

    def get_ticker(self, symbol: str) -> dict:
        data = self._get(f"/v2/tickers/{symbol}")
        return data.get("result", {})

    def get_available_expiries(self, underlying: str) -> list[str]:
        products = self.get_products()
        expiries = {
            item["settlement_time"][:10]
            for item in products
            if item.get("underlying_asset", {}).get("symbol") == underlying
            and item.get("contract_type") in ("call_options", "put_options")
            and item.get("settlement_time")
        }
        return sorted(expiries)

    def get_option_chain(self, underlying: str, expiry: str) -> list[dict]:
        year, month, day = expiry.split("-")
        data = self._get(
            "/v2/tickers",
            {
                "contract_types": "call_options,put_options",
                "underlying_asset_symbols": underlying,
                "expiry_date": f"{day}-{month}-{year}",
            },
        )
        return data.get("result", [])

    def _get(self, path: str, params: dict | None = None, signed: bool = False) -> Any:
        query = urlencode(params or {})
        headers = self._headers("GET", path, query=query) if signed else {"Accept": "application/json"}
        return self._request("GET", path, params=params, headers=headers)

    def _post(self, path: str, body: dict) -> Any:
        body_text = json.dumps(body)
        headers = self._headers("POST", path, body=body_text)
        return self._request("POST", path, data=body_text, headers=headers)

    def _headers(self, method: str, path: str, query: str = "", body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time()))
        signature = hmac.HMAC(
            self.api_secret.encode("utf-8"),
            f"{method}{timestamp}{path}{query}{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-key": self.api_key,
            "signature": signature,
            "timestamp": timestamp,
        }

    def _request(self, method: str, path: str, **kwargs) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.request(
                    method,
                    self.base_url + path,
                    timeout=self.timeout_seconds,
                    **kwargs,
                )
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
        raise last_error if last_error is not None else RuntimeError("request failed")
