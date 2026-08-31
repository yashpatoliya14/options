import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone

def fetch_binance_data(symbol="BTCUSDT", interval="5m", limit=1000, max_candles=105120):
    print(f"Fetching {max_candles} candles from Binance...")
    klines = []
    end_time = int(time.time() * 1000)
    
    while len(klines) < max_candles:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}&endTime={end_time}"
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Failed to fetch data: {response.text}")
            break
        data = response.json()
        if not data:
            break
        klines = data + klines
        end_time = data[0][0] - 1
        print(f"Fetched {len(klines)} candles...")
        time.sleep(0.1) # Rate limit protection

    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume", 
        "close_time", "quote_asset_volume", "number_of_trades", 
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit='ms')
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    csv_path = "data/BTCUSD_5m.csv"
    if not os.path.exists(csv_path):
        # Fetch ~1 year of 5m data
        df = fetch_binance_data(max_candles=105120)
        df.to_csv(csv_path, index=False)
        print(f"Saved to {csv_path}")
    else:
        print(f"{csv_path} already exists.")
