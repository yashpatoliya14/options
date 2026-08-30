"""
3H candle aggregation from 1H candles for Delta Exchange.

Delta Exchange API does not support '3h' resolution (only supports:
'5s','1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d','1w').

This module provides consistent 3H candle aggregation that works identically
in backtest and live modes to ensure strategy consistency.
"""
from typing import List, Dict, Any
import time
from strategy.supertrend import Candle


def aggregate_1h_to_3h(raw_1h_candles: List[Dict[str, Any]]) -> List[Candle]:
    """
    Aggregate 1H candles to 3H candles.
    
    Rules:
    1. Group 1H candles by exact 3H UTC boundaries (00:00, 03:00, 06:00...)
    2. Open = open of first candle in bucket
    3. High = maximum high in bucket
    4. Low = minimum low in bucket  
    5. Close = close of last candle in bucket
    6. Timestamp = exact close time of the 3H bucket (bucket_start + 3h)
    
    Args:
        raw_1h_candles: List of 1H candle dicts with 'time', 'open', 'high', 'low', 'close'
        
    Returns:
        List of 3H Candle objects
    """
    if not raw_1h_candles:
        return []
    
    # Ensure candles are sorted by timestamp (open time)
    sorted_candles = sorted(raw_1h_candles, key=lambda x: int(x["time"]))
    
    # Group by 3H boundaries (10800 seconds)
    buckets = {}
    for c in sorted_candles:
        ts = int(c["time"])
        bucket_start = (ts // 10800) * 10800
        if bucket_start not in buckets:
            buckets[bucket_start] = []
        buckets[bucket_start].append(c)
        
    candles_3h = []
    for bucket_start in sorted(buckets.keys()):
        chunk = buckets[bucket_start]
        candles_3h.append(Candle(
            timestamp=bucket_start + 10800,  # Exact close time of the 3H candle
            open=float(chunk[0]["open"]),
            high=max(float(c["high"]) for c in chunk),
            low=min(float(c["low"]) for c in chunk),
            close=float(chunk[-1]["close"]),
        ))
    
    return candles_3h


def is_3h_resolution_supported(client) -> bool:
    """
    Check if 3H resolution is supported by the exchange API.
    
    Returns:
        True if 3h resolution is supported, False otherwise
    """
    try:
        # Try to fetch a small window with 3h resolution
        now = int(time.time())
        test_data = client.get_historical_candles("BTCUSDT", "3h", now - 10800, now)
        return True
    except Exception:
        return False


def fetch_3h_candles(client, symbol: str, start_ts: int, end_ts: int) -> List[Candle]:
    """
    Fetch 3H candles, automatically aggregating from 1H if needed.
    
    This function handles the complexity of fetching 3H candles consistently
    whether the exchange supports 3h resolution natively or not.
    """
    # First try to get 3h candles directly
    try:
        raw_3h = client.get_historical_candles(symbol, "3h", start_ts, end_ts)
        if raw_3h:
            return [
                Candle(
                    timestamp=int(r["time"]),
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"])
                )
                for r in raw_3h
            ]
    except Exception:
        pass  # 3h not supported, fall back to 1h aggregation
    
    # Fetch 1h candles and aggregate to 3h
    raw_1h = client.get_historical_candles(symbol, "1h", start_ts, end_ts)
    return aggregate_1h_to_3h(raw_1h)


def get_latest_3h_candle(client, symbol: str) -> Candle:
    """
    Get the most recently closed 3H candle for live trading.
    
    This is used by live_algo.py to poll for new 3H candles.
    """
    now = int(time.time())
    # Look back enough to ensure we get at least one complete 3H candle
    start_ts = now - 4 * 3600  # 4 hours back
    
    candles = fetch_3h_candles(client, symbol, start_ts, now)
    if not candles:
        raise ValueError(f"No 3H candles found for {symbol}")
    
    # Return the most recent candle (should be closed)
    return candles[-1]