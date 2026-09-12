"""Read-only production market-data check.

This script uses the production public market-data endpoints only. It never
places, modifies, or cancels orders.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from live.delta_rest import DeltaRestClient


def main() -> int:
    load_dotenv()
    base_url = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")
    client = DeltaRestClient(base_url=base_url, api_key="", api_secret="")

    now = datetime.now(timezone.utc)
    end = int(now.timestamp())
    start = int((now - timedelta(hours=2)).timestamp())

    products = client.get_products()
    candles = client.get_historical_candles("BTCUSD", "1h", start, end)
    options = [
        product
        for product in products
        if product.get("contract_type") in {"call_options", "put_options"}
    ]

    print(f"Production read-only check passed: {len(products)} products")
    print(f"Option products: {len(options)}")
    print(f"BTCUSD 1h candles received: {len(candles)}")
    print("No orders were placed, modified, or cancelled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
