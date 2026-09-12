"""Read-only Delta testnet connectivity check.

This script never places orders. It refuses production mode and only checks
that the configured testnet credentials can read the product list.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from live.delta_rest import DeltaRestClient


TESTNET_URL = os.getenv("DELTA_TESTNET_URL", "https://cdn-ind.testnet.deltaex.org")


def main() -> int:
    load_dotenv()
    api_key = os.getenv("DELTA_API_KEY", "")
    api_secret = os.getenv("DELTA_API_SECRET", "")

    if not api_key or not api_secret:
        print("Missing DELTA_API_KEY or DELTA_API_SECRET")
        return 1

    if os.getenv("USE_TESTNET", "true").lower() != "true":
        print("Refusing to run: set USE_TESTNET=true before using test credentials")
        return 1

    client = DeltaRestClient(
        base_url=TESTNET_URL,
        api_key=api_key,
        api_secret=api_secret,
    )
    products = client.get_products()
    option_count = sum(
        1
        for product in products
        if product.get("contract_type") in {"call_options", "put_options"}
    )
    print(f"Testnet read-only check passed: {len(products)} products, {option_count} option products")
    print("No orders were placed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
