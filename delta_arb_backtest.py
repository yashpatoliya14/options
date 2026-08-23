"""
Delta Exchange - Short Strangle with Stop Loss (AlgoTest Classic)
======================================================
Simulates selling a 10% OTM Call and 10% OTM Put every 7 days.
If the option premium spikes by 30%, it hits the Stop Loss and closes that leg early.
Enforces realistic $20/leg slippage.

Output: Enriched CSV + Interactive HTML Dashboard
"""

import requests
import pandas as pd
import numpy as np
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from scipy.stats import norm

BASE_URL = "https://api.delta.exchange/v2"

# --- MASSIVE REAL-WORLD SLIPPAGE ---
SLIPPAGE_PCT = 0.08  # 8% of the option premium
OPTION_TAKER_FEE_BPS = 0.0003

STOP_LOSS_PCT = 0.30  # Close leg if premium rises 30%
OTM_PCT = 0.10        # Sell options 10% away from current price
CONSTANT_IV = 0.60    # 60% Implied Volatility

def black_scholes_price(spot, strike, T, iv, option_type="call", r=0.0):
    if T <= 1e-6:
        return max(0, spot - strike) if option_type == "call" else max(0, strike - spot)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    if option_type == "call":
        return spot * norm.cdf(d1) - strike * math.exp(-r * T) * norm.cdf(d2)
    else:
        return strike * math.exp(-r * T) * norm.cdf(-d2) - spot * norm.cdf(-d1)

def get_candles(symbol, resolution="1h", start=None, end=None):
    all_data = []
    chunk_start = start
    import time
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=30), end)
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": int(chunk_start.timestamp()),
            "end": int(chunk_end.timestamp()),
        }
        resp = requests.get(f"{BASE_URL}/history/candles", params=params)
        resp.raise_for_status()
        data = resp.json().get("result", [])
        if data:
            all_data.extend(data)
        chunk_start = chunk_end
        time.sleep(0.2)
        
    df = pd.DataFrame(all_data)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    return df

def run_strangle_backtest():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)  # 1 Year of 1-hour candles
    
    # Can configure this to ETHUSDT, SOLUSDT, etc.
    TRADING_SYMBOL = "BTCUSDT"
    
    print(f"Fetching 365 days of historical 1-hour candles for {TRADING_SYMBOL}...")
    try:
        df = get_candles(TRADING_SYMBOL, "1h", start, end)
        df['future'] = df['close']
    except Exception as e:
        print(f"API Error: {e}. Generating synthetic historical data.")
        np.random.seed(42)
        periods = 365 * 24
        times = [start + timedelta(hours=i) for i in range(periods)]
        future_prices = [65000]
        for _ in range(periods - 1):
            future_prices.append(future_prices[-1] * (1 + np.random.normal(0, 0.003)))
        df = pd.DataFrame({"time": times, "future": future_prices})

    if df.empty:
        print("Data is empty.")
        return

    print(f"Loaded {len(df)} historical data points.")

    results = []
    
    # State tracking
    in_trade = False
    entry_time = None
    expiry_time = None
    
    # Call leg
    call_active = False
    call_strike = 0
    call_entry_price = 0
    
    # Put leg
    put_active = False
    put_strike = 0
    put_entry_price = 0
    
    entry_notional = 0
    trade_pnl = 0
    
    # Track per-leg details for enriched output
    call_exit_premium = 0
    put_exit_premium = 0
    entry_slippage = 0
    exit_slippage_call = 0
    exit_slippage_put = 0
    total_fees = 0
    call_sl_hit = False
    put_sl_hit = False
    exit_underlying_price = 0

    print("Simulating Short Strangle with 30% Stop Loss...")

    for i, row in df.iterrows():
        current_time = row['time']
        current_price = row['future']
        
        if not in_trade:
            # Enter a new 7-day trade
            in_trade = True
            entry_time = current_time
            expiry_time = entry_time + timedelta(days=7)
            entry_notional = current_price
            
            call_strike = round(current_price * (1 + OTM_PCT), -2)
            put_strike = round(current_price * (1 - OTM_PCT), -2)
            
            call_entry_price = black_scholes_price(current_price, call_strike, 7/365, CONSTANT_IV, "call")
            put_entry_price = black_scholes_price(current_price, put_strike, 7/365, CONSTANT_IV, "put")
            
            call_active = True
            put_active = True
            trade_pnl = 0
            call_exit_premium = 0
            put_exit_premium = 0
            exit_slippage_call = 0
            exit_slippage_put = 0
            total_fees = 0
            call_sl_hit = False
            put_sl_hit = False
            exit_underlying_price = 0
            
            # Collect premium and pay entry slippage (8% of premium) & fees
            trade_pnl += (call_entry_price + put_entry_price)
            entry_slippage = (call_entry_price * SLIPPAGE_PCT) + (put_entry_price * SLIPPAGE_PCT)
            trade_pnl -= entry_slippage
            entry_fees = 2 * (entry_notional * OPTION_TAKER_FEE_BPS)
            trade_pnl -= entry_fees
            total_fees += entry_fees
            
        else:
            # Time to Expiry
            dte = (expiry_time - current_time).total_seconds() / (365 * 24 * 3600)
            
            if dte <= 0:
                # Expiry Reached (Cash Settled, no bid-ask slippage, only settlement fee)
                exit_reason = "Expiry"
                exit_time = current_time
                exit_underlying_price = current_price
                
                if call_active:
                    call_payout = max(0, current_price - call_strike)
                    call_exit_premium = call_payout
                    trade_pnl -= call_payout
                    fee = current_price * OPTION_TAKER_FEE_BPS
                    trade_pnl -= fee
                    total_fees += fee
                if put_active:
                    put_payout = max(0, put_strike - current_price)
                    put_exit_premium = put_payout
                    trade_pnl -= put_payout
                    fee = current_price * OPTION_TAKER_FEE_BPS
                    trade_pnl -= fee
                    total_fees += fee
                
                duration_hours = (exit_time - entry_time).total_seconds() / 3600
                
                results.append({
                    "Symbol": TRADING_SYMBOL,
                    "Entry Time": entry_time,
                    "Exit Time": exit_time,
                    "Exit Reason": exit_reason,
                    "Underlying Entry Price": entry_notional,
                    "Underlying Exit Price": exit_underlying_price,
                    "Call Strike": call_strike,
                    "Put Strike": put_strike,
                    "Call Entry Premium": call_entry_price,
                    "Put Entry Premium": put_entry_price,
                    "Total Premium Collected": (call_entry_price + put_entry_price),
                    "Call Exit Premium": call_exit_premium,
                    "Put Exit Premium": put_exit_premium,
                    "Entry Slippage": entry_slippage,
                    "Exit Slippage": exit_slippage_call + exit_slippage_put,
                    "Total Slippage Paid": entry_slippage + exit_slippage_call + exit_slippage_put,
                    "Fees Paid": total_fees,
                    "Call SL Hit": call_sl_hit,
                    "Put SL Hit": put_sl_hit,
                    "Trade Duration (hours)": round(duration_hours, 1),
                    "Net PnL": trade_pnl
                })
                in_trade = False
                continue
                
            # Check Stop Loss Intraday
            if call_active:
                current_call = black_scholes_price(current_price, call_strike, dte, CONSTANT_IV, "call")
                if current_call >= call_entry_price * (1 + STOP_LOSS_PCT):
                    # SL Hit - Close position early and pay 8% slippage on exit premium
                    call_exit_premium = current_call
                    exit_slippage_call = current_call * SLIPPAGE_PCT
                    trade_pnl -= current_call
                    trade_pnl -= exit_slippage_call
                    fee = current_price * OPTION_TAKER_FEE_BPS
                    trade_pnl -= fee
                    total_fees += fee
                    call_active = False
                    call_sl_hit = True
                    exit_reason = "Call SL Hit"
                    exit_time = current_time
                    exit_underlying_price = current_price
                    
            if put_active:
                current_put = black_scholes_price(current_price, put_strike, dte, CONSTANT_IV, "put")
                if current_put >= put_entry_price * (1 + STOP_LOSS_PCT):
                    # SL Hit - Close position early and pay 8% slippage on exit premium
                    put_exit_premium = current_put
                    exit_slippage_put = current_put * SLIPPAGE_PCT
                    trade_pnl -= current_put
                    trade_pnl -= exit_slippage_put
                    fee = current_price * OPTION_TAKER_FEE_BPS
                    trade_pnl -= fee
                    total_fees += fee
                    put_active = False
                    put_sl_hit = True
                    if call_sl_hit:
                        exit_reason = "Both SL Hit"
                    else:
                        exit_reason = "Put SL Hit"
                    exit_time = current_time
                    exit_underlying_price = current_price
                    
            # If both SL hit, close trade early
            if not call_active and not put_active:
                duration_hours = (exit_time - entry_time).total_seconds() / 3600
                
                results.append({
                    "Symbol": TRADING_SYMBOL,
                    "Entry Time": entry_time,
                    "Exit Time": exit_time,
                    "Exit Reason": exit_reason,
                    "Underlying Entry Price": entry_notional,
                    "Underlying Exit Price": exit_underlying_price,
                    "Call Strike": call_strike,
                    "Put Strike": put_strike,
                    "Call Entry Premium": call_entry_price,
                    "Put Entry Premium": put_entry_price,
                    "Total Premium Collected": (call_entry_price + put_entry_price),
                    "Call Exit Premium": call_exit_premium,
                    "Put Exit Premium": put_exit_premium,
                    "Entry Slippage": entry_slippage,
                    "Exit Slippage": exit_slippage_call + exit_slippage_put,
                    "Total Slippage Paid": entry_slippage + exit_slippage_call + exit_slippage_put,
                    "Fees Paid": total_fees,
                    "Call SL Hit": call_sl_hit,
                    "Put SL Hit": put_sl_hit,
                    "Trade Duration (hours)": round(duration_hours, 1),
                    "Net PnL": trade_pnl
                })
                in_trade = False

    res_df = pd.DataFrame(results)
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strangle_backtest_detailed_results.csv")
    res_df.to_csv(csv_path, index=False)
    print(f"\n\033[1;93mDetailed trade-by-trade breakdown saved to {csv_path}\033[0m")

    
    print("\n\033[1;96m" + "="*70 + "\033[0m")
    print("\033[1;97m BACKTEST RESULTS: SHORT STRANGLE + 30% SL + 8% SLIPPAGE \033[0m")
    print("\033[1;96m" + "="*70 + "\033[0m")
    
    if len(res_df) == 0:
        print("\033[93mNo trades were taken during this period.\033[0m")
        return

    # Print a colorful table of all trades
    print("\n\033[1;95m--- TRADE HISTORY ---\033[0m")
    print("\033[1;97m{:<25} | {:>15}\033[0m".format("Entry Time", "Net PnL (USD)"))
    print("\033[90m" + "-"*45 + "\033[0m")
    
    for _, row in res_df.iterrows():
        pnl = row['Net PnL']
        color = "\033[92m" if pnl > 0 else "\033[91m"
        pnl_str = f"{color}${pnl:,.2f}\033[0m"
        print(f"\033[97m{str(row['Entry Time']):<25}\033[0m | {pnl_str:>24}")
        
    print("\033[90m" + "-"*45 + "\033[0m")

    # Print Summary Metrics
    win_rate = (res_df["Net PnL"] > 0).mean() * 100
    cum_pnl = res_df['Net PnL'].sum()
    avg_pnl = res_df['Net PnL'].mean()
    best_trade = res_df['Net PnL'].max()
    worst_trade = res_df['Net PnL'].min()
    
    cum_color = "\033[1;92m" if cum_pnl > 0 else "\033[1;91m"
    
    print("\n\033[1;96m--- PERFORMANCE METRICS ---\033[0m")
    print(f"\033[97mTotal Periods:\033[0m       \033[93m{len(df):,}\033[0m (Hours)")
    print(f"\033[97mTotal Trades:\033[0m        \033[93m{len(res_df)}\033[0m")
    print(f"\033[97mWin Rate:\033[0m            \033[93m{win_rate:.1f}%\033[0m")
    print(f"\033[97mAverage PnL/Trade:\033[0m   \033[93m${avg_pnl:,.2f}\033[0m")
    print(f"\033[97mBest Trade:\033[0m          \033[92m+${best_trade:,.2f}\033[0m")
    print(f"\033[97mWorst Trade:\033[0m         \033[91m${worst_trade:,.2f}\033[0m")
    print("\n\033[1;97m---------------------------------------\033[0m")
    print(f"\033[1;97mCUMULATIVE PNL:\033[0m      {cum_color}${cum_pnl:,.2f}\033[0m")
    print("\033[1;97m---------------------------------------\033[0m\n")

    # MoM Breakdown
    print("\033[1;96m--- MONTH-ON-MONTH BREAKDOWN ---\033[0m")
    print("\033[1;97m{:<10} | {:>6} | {:>9} | {:>12}\033[0m".format("Month", "Trades", "Win Rate", "Net PnL"))
    print("\033[90m" + "-"*47 + "\033[0m")
    
    res_df['month'] = pd.to_datetime(res_df['Entry Time']).dt.strftime('%Y-%m')
    for month, group in res_df.groupby('month'):
        trades = len(group)
        m_win_rate = (group["Net PnL"] > 0).mean() * 100
        m_cum_pnl = group['Net PnL'].sum()
        m_color = "\033[92m" if m_cum_pnl > 0 else "\033[91m"
        m_pnl_str = f"{m_color}${m_cum_pnl:,.2f}\033[0m"
        print(f"\033[97m{month:<10}\033[0m | {trades:>6} | {m_win_rate:>8.1f}% | {m_pnl_str:>20}")
        
    print("\033[90m" + "-"*47 + "\033[0m\n")

    # Auto-launch dashboard
    print("\033[1;96mGenerating interactive dashboard...\033[0m")
    try:
        from backtest_dashboard import generate_dashboard
        dashboard_path = generate_dashboard(csv_path)
        print(f"\033[1;92mDashboard saved to: {dashboard_path}\033[0m")
        
        # Auto-open in browser
        import webbrowser
        webbrowser.open(f"file:///{dashboard_path}")
        print("\033[1;92mDashboard opened in browser!\033[0m")
    except ImportError:
        print("\033[93mbacktest_dashboard.py not found. Run it separately.\033[0m")
    except Exception as e:
        print(f"\033[91mDashboard generation failed: {e}\033[0m")

if __name__ == "__main__":
    run_strangle_backtest()
