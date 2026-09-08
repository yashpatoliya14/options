import os
import requests
import pandas as pd
from datetime import datetime, timezone
from colorama import init, Fore, Style

# Initialize colorama for Windows terminal colors
init()

from engine import StrategyEngine, StrategyParams
from backtest import BacktestRunner, HistoricalDataProvider, SimulatedClock, SimulatedExecutor

def fetch_binance_1h_data(symbol: str, max_candles: int = 20000) -> pd.DataFrame:
    """Fetch 1H candles from Binance directly, with local CSV caching."""
    cache_file = f"{symbol}_1h_cache.csv"
    
    # 1. Load existing cache if available
    if os.path.exists(cache_file):
        print(f"{Fore.CYAN}Loading cached data from {cache_file}...{Style.RESET_ALL}")
        df_cache = pd.read_csv(cache_file)
        df_cache['timestamp'] = pd.to_datetime(df_cache['timestamp'], utc=True)
        # Find latest timestamp
        latest_ts = int(df_cache['timestamp'].max().timestamp() * 1000)
    else:
        df_cache = pd.DataFrame()
        latest_ts = None

    print(f"{Fore.CYAN}Fetching new 1H data for {symbol}...{Style.RESET_ALL}")
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_klines = []
    end_time = None
    limit = 1000
    
    # If we have cache, we fetch forward. But Binance klines with startTime fetches forward.
    # To fetch recent history if no cache, we fetch backward using endTime.
    if latest_ts:
        # Fetch forward from latest_ts
        start_time = latest_ts + 1  # add 1ms
        while True:
            params = {"symbol": symbol, "interval": "1h", "limit": limit, "startTime": start_time}
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if not data:
                    break
                    
                all_klines.extend(data)
                start_time = data[-1][0] + 1
                print(f"  Fetched {len(all_klines)} new candles...", end="\r")
                
                if len(data) < limit:
                    break
            except Exception as e:
                print(f"{Fore.RED}Error fetching data: {e}{Style.RESET_ALL}")
                break
    else:
        # Fetch backward from now
        while len(all_klines) < max_candles:
            params = {"symbol": symbol, "interval": "1h", "limit": limit}
            if end_time:
                params["endTime"] = end_time
                
            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if not data:
                    break
                    
                all_klines.extend(data)
                end_time = data[0][0] - 1
                print(f"  Fetched {len(all_klines)} candles...", end="\r")
            except Exception as e:
                print(f"{Fore.RED}Error fetching data: {e}{Style.RESET_ALL}")
                break
            
    print()
    
    # Process new data
    if all_klines:
        df_new = pd.DataFrame(all_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df_new['timestamp'] = pd.to_datetime(df_new['timestamp'], unit='ms', utc=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df_new[col] = pd.to_numeric(df_new[col], errors='coerce')
        df_new = df_new[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        # Combine cache and new data
        df = pd.concat([df_cache, df_new], ignore_index=True)
    else:
        df = df_cache

    if df.empty:
        return df
        
    df = df.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    
    # Save back to cache
    df.to_csv(cache_file, index=False)
    
    # Return requested window
    return df.tail(max_candles).reset_index(drop=True)


def print_dashboard(result, symbol: str):
    """Print a beautiful terminal dashboard."""
    rep = result.report
    trades = result.trades
    
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}================================================================={Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}      BTCUSD 1H DIRECTIONAL OPTIONS STRATEGY - BACKTEST RESULT    {Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}================================================================={Style.RESET_ALL}\n")
    
    # Overview
    print(f"{Fore.CYAN}{Style.BRIGHT}> OVERVIEW{Style.RESET_ALL}")
    print(f"  Asset:               {Fore.WHITE}{symbol}{Style.RESET_ALL}")
    print(f"  Total Trades:        {Fore.WHITE}{rep['trade_count']}{Style.RESET_ALL}")
    print(f"  Trades / Day:        {Fore.WHITE}{rep['avg_trades_per_day']:.2f}{Style.RESET_ALL}")
    print(f"  Trades / Month:      {Fore.WHITE}{rep['avg_trades_per_month']:.1f}{Style.RESET_ALL}")
    print(f"  Trades / Year:       {Fore.WHITE}{rep['avg_trades_per_year']:.1f}{Style.RESET_ALL}")
    print(f"  Total PnL:           {Fore.GREEN if rep['total_pnl'] > 0 else Fore.RED}${rep['total_pnl']:.2f}{Style.RESET_ALL}")
    print(f"  Win Rate:            {Fore.WHITE}{rep['win_rate']*100:.2f}%{Style.RESET_ALL}")
    
    wins = len([t for t in trades if t.realized_pnl > 0])
    losses = rep['trade_count'] - wins
    
    print(f"  Max Drawdown:        {Fore.RED}${rep['max_drawdown']:.2f}{Style.RESET_ALL}")
    print(f"  Wins / Losses:       {Fore.GREEN}{wins}{Style.RESET_ALL} / {Fore.RED}{losses}{Style.RESET_ALL}")
    print(f"  Average Win:         {Fore.GREEN}${rep['avg_win']:.2f}{Style.RESET_ALL}")
    print(f"  Average Loss:        {Fore.RED}${rep['avg_loss']:.2f}{Style.RESET_ALL}")
    
    # Trade Breakdown
    buys = [t for t in trades if t.signal_type == "buy"]
    sells = [t for t in trades if t.signal_type == "sell"]
    
    buy_wins = sum(1 for t in buys if t.realized_pnl > 0)
    sell_wins = sum(1 for t in sells if t.realized_pnl > 0)
    
    print(f"\n{Fore.CYAN}{Style.BRIGHT}> BREAKDOWN BY DIRECTION{Style.RESET_ALL}")
    print(f"  Bull Put Spreads (Buys):  {len(buys)} trades, {(buy_wins/len(buys)*100) if buys else 0:.1f}% win rate")
    print(f"  Bear Put Spreads (Sells): {len(sells)} trades, {(sell_wins/len(sells)*100) if sells else 0:.1f}% win rate")
    
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}================================================================={Style.RESET_ALL}\n")
    
    # Print First 100 Trades
    print(f"{Fore.CYAN}{Style.BRIGHT}> FIRST 100 TRADES LOG{Style.RESET_ALL}")
    print("-" * 110)
    print(f"{'Entry Time':<20} | {'Exit Time':<20} | {'Dir':<4} | {'Spread':<15} | {'Exp':<8} | {'Credit/Debit':<12} | {'PnL':<8} | {'Reason':<15}")
    print("-" * 110)
    
    for t in trades[:100]:
        # Format
        entry = t.entry_time.strftime("%Y-%m-%d %H:00")
        exit_time = t.exit_time.strftime("%Y-%m-%d %H:00")
        direction = f"{Fore.GREEN}BULL{Style.RESET_ALL}" if t.signal_type == "buy" else f"{Fore.RED}BEAR{Style.RESET_ALL}"
        spread = f"{t.long_strike}/{t.short_strike}"
        
        # Credit/Debit
        if t.credit_received > 0:
            c_d = f"CR: {t.credit_received:.1f}"
        else:
            c_d = f"DB: {abs(t.credit_received):.1f}"
            
        pnl_color = Fore.GREEN if t.realized_pnl > 0 else Fore.RED
        pnl = f"{pnl_color}${t.realized_pnl:>6.1f}{Style.RESET_ALL}"
        
        print(f"{entry:<20} | {exit_time:<20} | {direction:<13} | {spread:<15} | {t.expiry_label:<8} | {c_d:<12} | {pnl:<17} | {t.exit_reason:<15}")
        
    if len(trades) > 100:
        print(f"... and {len(trades) - 100} more trades.")
        
    print("-" * 110)


def main():
    SYMBOL = "BTCUSDT"
    
    # 1. Fetch 1H Data
    df = fetch_binance_1h_data(SYMBOL, max_candles=20000) # ~833 days of 1H data
    if df.empty:
        return
        
    print(f"\n{Fore.GREEN}Data ready. Initializing Backtest Engine...{Style.RESET_ALL}")
    
    # 2. Configure Strategy (Optimal BTC Combo)
    params = StrategyParams(
        underlying="BTC",
        candle_symbol=SYMBOL,
        spread_type="directional",
        # Optimal combo config
        adx_trend_threshold=20.0,
        ema_trend_fast=9,
        ema_trend_slow=21,
        rsi_period=14,
        rsi_oversold=35.0,
        rsi_overbought=65.0,
        
        # Risk / Trade Config
        tp_pct=0.50,
        sl_pct=1.00,
        qty=1,
        slippage_pct=0.0025,
        commission_per_leg=0.0,
        cooldown_seconds=3600, # 1 hour cooldown
        early_exit_minutes=60,
        expiry_cutoff_hour=9,
        assumed_iv=0.55,
        spread_width=200, 
    )
    
    clock = SimulatedClock(df["timestamp"].iloc[0].to_pydatetime())
    provider = HistoricalDataProvider(df, clock, params)
    executor = SimulatedExecutor(provider, params)
    engine = StrategyEngine(params)
    
    runner = BacktestRunner(engine, provider, executor, params, lookback=100)
    
    # 3. Run Backtest
    print(f"{Fore.CYAN}Running Backtest Loop...{Style.RESET_ALL}")
    result = runner.run()
    
    # 4. Print Dashboard
    print_dashboard(result, "BTC")


if __name__ == "__main__":
    main()
