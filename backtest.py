import os
import pandas as pd
from engine import StrategyEngine, StrategyParams
from backtest import BacktestRunner, HistoricalDataProvider, SimulatedClock, SimulatedExecutor

def run_backtest_and_report():
    print("Setting up backtest...")
    csv_path = "data/BTCUSD_5m.csv"
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found.")
        return

    # Use parameters that match the generated strike_step of 600
    params = StrategyParams(
        ema_fast=288,
        ema_slow=864,
        adx_min=25.0,
        credit_min=50,
        credit_max=250,
        spread_width=600,
        tp_pct=1.00,
        sl_pct=100.0,
        qty=1,
        slippage_pct=0.0025,
        commission_per_leg=0,
        cooldown_seconds=7200,
        option_data_mode="reconstructed",
    )

    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    # Run the full year! (It takes about 3 minutes)
    clock = SimulatedClock(df["timestamp"].iloc[0].to_pydatetime())
    provider = HistoricalDataProvider(df, clock, params)
    executor = SimulatedExecutor(provider, params)
    engine = StrategyEngine(params)
    
    runner = BacktestRunner(engine, provider, executor, params)
    
    print("Running backtest... This may take a moment.")
    result = runner.run()
    
    # Process results
    trades = result.trades
    if not trades:
        print("No trades executed during the backtest.")
        return

    trade_data = []
    for t in trades:
        trade_data.append({
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "pnl": t.realized_pnl,
            "win": 1 if t.realized_pnl > 0 else 0
        })
    
    tdf = pd.DataFrame(trade_data)
    tdf["entry_time"] = pd.to_datetime(tdf["entry_time"])
    tdf.set_index("entry_time", inplace=True)
    tdf["equity"] = tdf["pnl"].cumsum() + 10000 # Assume 10k starting equity
    
    # Yearly
    print("\n--- Year by Year Performance ---")
    yearly = tdf.groupby(tdf.index.year).agg(
        trades=("pnl", "count"),
        wins=("win", "sum"),
        pnl=("pnl", "sum")
    )
    yearly["winrate"] = (yearly["wins"] / yearly["trades"] * 100).round(2).astype(str) + "%"
    yearly["return"] = (yearly["pnl"] / 10000 * 100).round(2).astype(str) + "%"
    print(yearly[["trades", "winrate", "pnl", "return"]])

    # Monthly
    print("\n--- Month by Month Performance ---")
    tdf["year_month"] = tdf.index.to_period("M")
    monthly = tdf.groupby("year_month").agg(
        trades=("pnl", "count"),
        wins=("win", "sum"),
        pnl=("pnl", "sum")
    )
    monthly["winrate"] = (monthly["wins"] / monthly["trades"] * 100).round(2).astype(str) + "%"
    print(monthly[["trades", "winrate", "pnl"]])

    # Overall Equity
    print("\n--- Overall Summary ---")
    print(f"Total Trades: {result.report['trade_count']}")
    print(f"Total PnL: ${result.report['total_pnl']:.2f}")
    print(f"Win Rate: {result.report['win_rate']*100:.2f}%")
    print(f"Max Drawdown: ${result.report['max_drawdown']:.2f}")
    print(f"Final Equity: ${tdf['equity'].iloc[-1]:.2f} (starting $10000)")

if __name__ == "__main__":
    run_backtest_and_report()
