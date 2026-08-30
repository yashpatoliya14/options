"""
backtest.py -- replays historical 3h candles through the SAME engine.py used
by live_algo.py, using SimulatedBroker for fills.

IMPORTANT: run_diagnostic.py must be run first (with real API credentials) to
determine whether 'realistic' (real recorded option premiums) or
'reconstructed' (Black-Scholes theoretical) data mode is actually available.
Until that's confirmed, this defaults to 'reconstructed' mode, which is
explicitly an APPROXIMATION -- see exchange/simulated_broker.py docstring.

Usage:
    python backtest.py --mode reconstructed --days 180
    python backtest.py --mode realistic --start 2024-01-01 --end 2024-12-31
"""
import argparse
import json
import time
import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from collections import Counter
from dataclasses import replace
from typing import List

from config import CONFIG
from engine import StrategyEngine, EngineEvent
from strategy.supertrend import Candle
from exchange.simulated_broker import SimulatedBroker, ReconstructedOptionDataSource, HistoricalOptionDataSource
from exchange.delta_client import DeltaClient


def fetch_underlying_candles(start_ts: int, end_ts: int, resolution: str = "2h") -> List[Candle]:
    """
    Pulls real historical underlying candles from Delta natively (2h supported).
    """
    client = DeltaClient(CONFIG)
    
    # Extract duration in seconds
    duration = 7200 if resolution == "2h" else 3600
    
    def fetch_chunked(res: str) -> List[dict]:
        # Exchange limit is typically 2000 candles per request.
        # 100 days of 2h candles = 1200 candles.
        chunk_seconds = 60 * 86400 if res == "1h" else 100 * 86400
        rows = []
        cursor = start_ts
        while cursor < end_ts:
            chunk_end = min(cursor + chunk_seconds, end_ts)
            rows.extend(client.get_historical_candles(
                CONFIG.underlying_symbol, res, cursor, chunk_end
            ))
            cursor = chunk_end + 1
        return sorted({int(r["time"]): r for r in rows}.values(), key=lambda r: int(r["time"]))

    raw = fetch_chunked(resolution)
    if not raw:
        return []
    
    candles = []
    for r in raw:
        candles.append(Candle(
            timestamp=int(r["time"]) + duration,
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"])
        ))
    
    print(f"Fetched {len(candles)} {resolution} candles natively.")
    return candles


def run_backtest(candles: List[Candle], data_mode: str, cfg, initial_margin: float = 1000.0, leverage: float = 1.0) -> dict:
    if not candles:
        return summarize([], data_mode, cfg, initial_margin, leverage)

    events: List[EngineEvent] = []

    def price_series(ts: int) -> float:
        # nearest candle close at or before ts
        best = None
        for c in candles:
            if c.timestamp <= ts:
                best = c
            else:
                break
        return best.close if best else candles[0].close

    if data_mode == "reconstructed":
        data_source = ReconstructedOptionDataSource(price_series)
    else:
        data_source = HistoricalOptionDataSource(cache_dir="data/options_cache")

    broker = SimulatedBroker(cfg, data_source)
    engine = StrategyEngine(cfg, broker, on_event=lambda e: events.append(e))

    for c in candles:
        broker.set_clock(c.timestamp)
        
        # --- Intra-candle Stop-Loss Evaluation ---
        # Evaluate SL using the worst-case underlying price during this 3H candle.
        if engine.current_position is not None:
            pos = engine.current_position
            # For a short PUT, the highest premium (worst for us) occurs when spot is LOW.
            # For a short CALL, the highest premium (worst for us) occurs when spot is HIGH.
            worst_spot = c.low if pos.option_type == "put" else c.high
            
            # Fetch reconstructed quote at the worst spot to get worst premium
            # Extract underlying from symbol: e.g. "P-BTCUSD-40000-2024-01-01" -> "BTCUSD"
            # The symbol format in simulated_broker is: "P-BTCUSD-..."
            symbol_parts = pos.symbol.split('-')
            underlying_sym = symbol_parts[1] if len(symbol_parts) > 1 else cfg.underlying_symbol
            
            quotes = broker.get_option_chain(underlying_sym, pos.expiry, timestamp=c.timestamp, spot_override=worst_spot)
            
            for q in quotes:
                if q.strike == pos.strike and q.option_type == pos.option_type:
                    engine.evaluate_stop_loss(q.premium, c.timestamp)
                    break
        # -----------------------------------------

        engine.on_candle_close(c)

    # Realize any open position at the end of the dataset so PnL isn't left unrealized.
    broker.set_clock(candles[-1].timestamp)
    engine.finalize(candles[-1].timestamp)

    return summarize(events, data_mode, cfg, initial_margin, leverage)


def summarize(events: List[EngineEvent], data_mode: str, cfg, initial_margin: float = 1000.0, leverage: float = 1.0) -> dict:
    trades = []
    open_trade = None
    for e in events:
        if e.kind == "trade_entry":
            open_trade = dict(e.payload)
            open_trade["entry_timestamp"] = e.timestamp
        elif e.kind == "trade_exit" and open_trade is not None:
            entry_premium = open_trade["premium"]
            exit_premium = e.payload.get("exit_premium") or 0.0
            quantity = open_trade["quantity"]
            # selling an option: profit = (entry_premium - exit_premium) * quantity * contract_value
            # (you collect premium on entry, pay it back to close)
            gross_pnl = (entry_premium - exit_premium) * quantity * cfg.contract_value_underlying
            
            # Calculate fees based on notional value (strike * contract value * quantity)
            # Entry and exit are both taker orders in this strategy
            notional_value = open_trade["strike"] * cfg.contract_value_underlying * quantity
            fee_rate = cfg.fee_pct / 100.0  # Convert percentage to decimal
            entry_fee = notional_value * fee_rate
            exit_fee = notional_value * fee_rate
            fees = entry_fee + exit_fee
            
            net_pnl = gross_pnl - fees
            trades.append({
                "entry_timestamp": open_trade["entry_timestamp"],
                "exit_timestamp": e.timestamp,
                "signal": open_trade["signal"],
                "option_type": open_trade["option_type"],
                "strike": open_trade["strike"],
                "expiry": open_trade["expiry"],
                "entry_premium": entry_premium,
                "exit_premium": exit_premium,
                "quantity": quantity,
                "gross_pnl": round(gross_pnl, 2),
                "fees": round(fees, 2),
                "net_pnl": round(net_pnl, 2),
                "holding_time_hours": round((e.timestamp - open_trade["entry_timestamp"]) / 3600, 2),
                "exit_reason": e.payload.get("reason"),
                "data_mode": data_mode,
            })
            open_trade = None

    total = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_pnl_sum = sum(t["gross_pnl"] for t in trades)
    fees_sum = sum(t["fees"] for t in trades)
    net_pnl_sum = sum(t["net_pnl"] for t in trades)
    signal_changes = sum(1 for e in events if e.kind == "signal" and e.payload.get("signal_change"))
    skip_events = [e for e in events if e.kind == "trade_skipped"]
    skip_reason_counts = dict(Counter(e.payload.get("reason") or "unknown" for e in skip_events))
    skipped_premiums = [
        e.payload.get("best_premium_seen") for e in skip_events
        if e.payload.get("best_premium_seen") is not None
    ]

    # --- Leverage-aware equity curve ---
    # With leverage, your actual capital is `initial_margin` but you control
    # `initial_margin * leverage` worth of effective margin. The per-trade PnL
    # is already computed using the leveraged position size (because margin_budget
    # is set to initial_margin * leverage). We track how the ACTUAL capital
    # (initial_margin) changes and detect liquidation (equity <= 0).
    effective_margin = initial_margin * leverage
    equity_curve = []
    running = initial_margin  # Your real money
    liquidated = False
    liquidation_trade_index = None
    
    for i, t in enumerate(trades):
        if liquidated:
            # After liquidation, no more trades can happen
            t["cumulative_pnl"] = 0.0
            t["liquidated"] = True
            continue
            
        running += t["net_pnl"]
        
        if running <= 0:
            running = 0.0
            liquidated = True
            liquidation_trade_index = i
            t["cumulative_pnl"] = 0.0
            t["liquidated"] = True
            t["exit_reason"] = "LIQUIDATED"
            equity_curve.append({"timestamp": t["exit_timestamp"], "equity": 0.0})
        else:
            t["cumulative_pnl"] = round(running, 2)
            t["liquidated"] = False
            equity_curve.append({"timestamp": t["exit_timestamp"], "equity": round(running, 2)})

    # Count only trades that actually executed (before liquidation)
    active_trades = trades if not liquidated else trades[:liquidation_trade_index + 1]
    active_total = len(active_trades)
    active_wins = [t for t in active_trades if t["net_pnl"] > 0]
    active_losses = [t for t in active_trades if t["net_pnl"] <= 0]
    active_net_pnl = sum(t["net_pnl"] for t in active_trades)

    # max drawdown
    peak = float("-inf")
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["equity"])
        max_dd = min(max_dd, pt["equity"] - peak)

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    avg_holding = (sum(t["holding_time_hours"] for t in trades) / total) if total else 0.0

    # simple Sharpe on per-trade returns (not annualized -- flagged as approximate)
    import statistics
    returns = [t["net_pnl"] for t in trades]
    sharpe = 0.0
    if len(returns) > 1 and statistics.pstdev(returns) > 0:
        sharpe = statistics.mean(returns) / statistics.pstdev(returns)

    monthly_pnl = {}
    import datetime
    for t in trades:
        if t.get("liquidated") and t["cumulative_pnl"] == 0.0 and t != active_trades[-1] if active_trades else True:
            continue  # Skip post-liquidation phantom trades
        month = datetime.datetime.fromtimestamp(t["exit_timestamp"], datetime.UTC).strftime("%Y-%m")
        monthly_pnl[month] = round(monthly_pnl.get(month, 0.0) + t["net_pnl"], 2)

    # Compute final equity and return on actual capital
    final_equity = running
    total_return_pct = ((final_equity - initial_margin) / initial_margin * 100) if initial_margin > 0 else 0.0

    summary = {
        "data_mode": data_mode,
        "data_mode_warning": (
            None if data_mode == "realistic" else
            "RECONSTRUCTED MODE: option premiums are Black-Scholes theoretical "
            "estimates using an assumed flat implied volatility, not real "
            "historical bid/ask data. Treat P&L figures as directional/illustrative "
            "only, not a reliable estimate of real trading results."
        ),
        # --- Leverage info ---
        "leverage": leverage,
        "initial_capital": round(initial_margin, 2),
        "effective_margin": round(effective_margin, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "liquidated": liquidated,
        "liquidation_trade_index": liquidation_trade_index,
        # --- Standard stats ---
        "total_trades": total,
        "trades_before_liquidation": active_total if liquidated else total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(100 * len(wins) / total, 2) if total else 0.0,
        "gross_pnl": round(gross_pnl_sum, 2),
        "total_fees": round(fees_sum, 2),
        "net_pnl": round(net_pnl_sum, 2),
        "average_trade_pnl": round(net_pnl_sum / total, 2) if total else 0.0,
        "max_drawdown": round(max_dd, 2),
        "profit_factor": profit_factor,
        "sharpe_ratio_per_trade": round(sharpe, 3),
        "average_holding_time_hours": round(avg_holding, 2),
        "largest_winning_trade": max((t["net_pnl"] for t in trades), default=0.0),
        "largest_losing_trade": min((t["net_pnl"] for t in trades), default=0.0),
        "monthly_pnl": monthly_pnl,
        "signal_changes": signal_changes,
        "skipped_signals": len(skip_events),
        "skip_reasons": skip_reason_counts,
        "max_skipped_premium_seen": round(max(skipped_premiums), 2) if skipped_premiums else None,
    }

    return {"trades": trades, "summary": summary, "equity_curve": equity_curve}


def print_beautiful_output(result: dict) -> None:
    console = Console()
    summary = result["summary"]
    trades = result["trades"]
    
    if summary.get("data_mode_warning"):
        console.print(Panel(f"[yellow]{summary['data_mode_warning']}[/yellow]", title="Warning", border_style="yellow"))

    # Print Trades Table
    if trades:
        table = Table(title="Trades Executed", show_header=True, header_style="bold magenta")
        table.add_column("Entry Time", style="dim", no_wrap=True)
        table.add_column("Exit Time", style="dim", no_wrap=True)
        table.add_column("Type", justify="center")
        table.add_column("Strike", justify="right", no_wrap=True)
        table.add_column("Entry Prem", justify="right")
        table.add_column("Exit Prem", justify="right")
        table.add_column("Qty", justify="right")
        table.add_column("Gross PnL", justify="right")
        table.add_column("Fees", justify="right")
        table.add_column("Net PnL", justify="right", style="bold")
        table.add_column("Cum PnL", justify="right", style="bold cyan", no_wrap=True)
        table.add_column("Reason", style="cyan")

        import datetime
        for t in trades:
            # Using local time instead of UTC, and including the year
            entry_dt = datetime.datetime.fromtimestamp(t["entry_timestamp"]).strftime("%Y-%m-%d %H:%M")
            exit_dt = datetime.datetime.fromtimestamp(t["exit_timestamp"]).strftime("%Y-%m-%d %H:%M")
            
            pnl_style = "green" if t["net_pnl"] > 0 else "red"
            
            table.add_row(
                entry_dt,
                exit_dt,
                f"{t['option_type']}",
                str(t["strike"]),
                f"{t['entry_premium']:.2f}",
                f"{t['exit_premium']:.2f}",
                str(t["quantity"]),
                f"{t['gross_pnl']:.2f}",
                f"{t['fees']:.2f}",
                f"[{pnl_style}]{t['net_pnl']:.2f}[/{pnl_style}]",
                f"{t.get('cumulative_pnl', 0):.2f}",
                t.get("exit_reason", "")
            )
        console.print(table)
    
    # Print Summary Panel
    # --- Leverage Panel ---
    leverage = summary.get('leverage', 1.0)
    if leverage > 1:
        lev_color = "red bold" if summary.get('liquidated') else "green bold"
        lev_text = (
            f"[bold]Leverage:[/bold] {leverage:.0f}x\n"
            f"[bold]Initial Capital:[/bold] ${summary['initial_capital']:,.2f}\n"
            f"[bold]Effective Margin (Capital × Leverage):[/bold] ${summary['effective_margin']:,.2f}\n"
            f"[bold]Final Equity:[/bold] [{lev_color}]${summary['final_equity']:,.2f}[/{lev_color}]\n"
            f"[bold]Total Return on Capital:[/bold] [{lev_color}]{summary['total_return_pct']:+.2f}%[/{lev_color}]"
        )
        if summary.get('liquidated'):
            lev_text += f"\n[red bold]⚠ LIQUIDATED at trade #{summary['liquidation_trade_index'] + 1} -- equity hit zero![/red bold]"
            lev_text += f"\n[yellow]Trades executed before liquidation: {summary['trades_before_liquidation']}[/yellow]"
        console.print(Panel(lev_text, title="💰 Leverage Summary", border_style="yellow", expand=False))

    summary_text = (
        f"[bold]Total Trades:[/bold] {summary['total_trades']} "
        f"([green]W: {summary['winning_trades']}[/green] | [red]L: {summary['losing_trades']}[/red])\n"
        f"[bold]Win Rate:[/bold] {summary['win_rate_pct']}%\n"
        f"[bold]Gross PnL:[/bold] {summary['gross_pnl']:.2f}\n"
        f"[bold]Total Fees:[/bold] {summary['total_fees']:.2f}\n"
        f"[bold]Net PnL:[/bold] {summary['net_pnl']:.2f}\n"
        f"[bold]Avg Trade PnL:[/bold] {summary['average_trade_pnl']:.2f}\n"
        f"[bold]Max Drawdown:[/bold] {summary['max_drawdown']:.2f}\n"
        f"[bold]Profit Factor:[/bold] {summary['profit_factor']:.2f}\n"
        f"[bold]Sharpe (approx):[/bold] {summary['sharpe_ratio_per_trade']:.3f}\n"
        f"[bold]Avg Hold Time:[/bold] {summary['average_holding_time_hours']:.1f} hrs\n"
        f"[bold]Skipped Signals:[/bold] {summary['skipped_signals']}"
    )
    
    console.print(Panel(summary_text, title="Backtest Summary", border_style="blue", expand=False))


def run_year_backtest(year: int, initial_capital: float, cfg, data_mode: str = "reconstructed", leverage: float = 1.0) -> dict:
    """
    Run backtest for a specific year.
    
    Args:
        year: Year to backtest (e.g., 2024, 2025, 2026)
        initial_capital: Initial capital in USD
        cfg: The strategy configuration to use
        data_mode: 'reconstructed' or 'realistic'
        leverage: Leverage multiplier (e.g. 100 = 100x). Capital * leverage = effective margin.
        
    Returns:
        Backtest results dictionary
    """
    import datetime
    
    # Define year boundaries
    start_date = datetime.datetime(year, 1, 1)
    end_date = datetime.datetime(year, 12, 31, 23, 59, 59)
    
    start_ts = int(start_date.timestamp())
    end_ts = int(end_date.timestamp())
    
    # Ensure we don't include future data
    current_ts = int(time.time())
    if end_ts > current_ts:
        end_ts = current_ts
        
    console = Console()
    console.print(f"[cyan]Running {year} backtest from {start_date.date()} to {end_date.date()}[/cyan]")
    console.print(f"[cyan]Initial Capital: ${initial_capital:,.2f}[/cyan]")
    if leverage > 1:
        console.print(f"[yellow]Leverage: {leverage:.0f}x → Effective Margin: ${initial_capital * leverage:,.2f}[/yellow]")
    console.print(f"[cyan]Data Mode: {data_mode}[/cyan]")
    
    # Display fee information
    from utils.fee_calculator import FeeCalculator
    fee_calc = FeeCalculator(cfg)
    fee_rate = fee_calc.get_fee_rate(is_taker=True)
    console.print(f"[cyan]Fee Rate: {fee_rate*100:.4f}% (Taker)[/cyan]")
    console.print(f"[cyan]Fee Source: Delta Exchange API (from diagnostic report)[/cyan]")
    
    # Fetch candles
    console.print(f"[cyan]Fetching underlying candles...[/cyan]")
    candles = fetch_underlying_candles(start_ts, end_ts)
    
    if not candles:
        console.print(f"[red]No candles found for {year}. Check API connectivity and data availability.[/red]")
        return {"trades": [], "summary": {}, "equity_curve": []}
        
    console.print(f"[green]Loaded {len(candles)} 3h candles for {year}.[/green]")
    
    # Run backtest with leverage
    result = run_backtest(candles, data_mode, cfg, initial_capital, leverage)
    
    # Save results
    save_backtest_results(year, result, initial_capital, cfg, data_mode, fee_rate, leverage)
    
    return result


def save_backtest_results(year: int, result: dict, initial_capital: float, cfg,
                          data_mode: str, fee_rate: float, leverage: float = 1.0):
    """
    Save backtest results to organized directory structure.
    """
    import os
    import json
    import csv
    import datetime
    
    # Create directory structure
    results_dir = f"backtest_results/{year}"
    os.makedirs(results_dir, exist_ok=True)
    
    # Prepare metadata
    metadata = {
        "year": year,
        "initial_capital": initial_capital,
        "leverage": leverage,
        "effective_margin": initial_capital * leverage,
        "data_mode": data_mode,
        "fee_rate": fee_rate,
        "fee_source": "Delta Exchange API (from diagnostic report)",
        "strategy_parameters": {
            "timeframe": cfg.timeframe,
            "supertrend_atr_period": cfg.supertrend_atr_period,
            "supertrend_multiplier": cfg.supertrend_multiplier,
            "min_premium_usd": cfg.min_premium_usd,
            "margin_budget_usd": cfg.margin_budget_usd,
            "contract_value_underlying": cfg.contract_value_underlying,
        },
        "execution_timestamp": datetime.datetime.now().isoformat(),
        "data_range": {
            "start": datetime.datetime.fromtimestamp(result["trades"][0]["entry_timestamp"]).isoformat() if result["trades"] else None,
            "end": datetime.datetime.fromtimestamp(result["trades"][-1]["exit_timestamp"]).isoformat() if result["trades"] else None,
        }
    }
    
    # Save trades as CSV
    trades_path = os.path.join(results_dir, "trades.csv")
    if result["trades"]:
        with open(trades_path, "w", newline="") as f:
            fieldnames = list(result["trades"][0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(result["trades"])
    
    # Save summary as JSON
    summary_path = os.path.join(results_dir, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(result["summary"], f, indent=2)
        
    # Save metadata
    metadata_path = os.path.join(results_dir, "metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
        
    # Save equity curve
    equity_path = os.path.join(results_dir, "equity_curve.csv")
    if result.get("equity_curve"):
        with open(equity_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "equity"])
            writer.writeheader()
            writer.writerows(result["equity_curve"])
            
        # Attempt to create a plot
        try:
            import matplotlib.pyplot as plt
            dates = [datetime.datetime.fromtimestamp(pt["timestamp"]) for pt in result["equity_curve"]]
            equities = [pt["equity"] for pt in result["equity_curve"]]
            
            plt.figure(figsize=(10, 6))
            plt.plot(dates, equities, label='Equity', color='blue')
            plt.title(f'Equity Curve ({year})')
            plt.xlabel('Date')
            plt.ylabel('Portfolio Equity (USD)')
            plt.grid(True)
            plt.legend()
            
            plot_path = os.path.join(results_dir, "equity_curve.png")
            plt.savefig(plot_path)
            plt.close()
        except ImportError:
            pass  # matplotlib not installed
    
    # Save report as text
    report_path = os.path.join(results_dir, "report.txt")
    generate_text_report(year, result, metadata, report_path)
    
    print(f"Results saved to {results_dir}/")


def generate_text_report(year: int, result: dict, metadata: dict, filepath: str):
    """
    Generate a comprehensive text report.
    """
    summary = result["summary"]
    trades = result["trades"]
    
    with open(filepath, "w") as f:
        f.write(f"BACKTEST REPORT - {year}\n")
        f.write("=" * 50 + "\n\n")
        
        f.write("STRATEGY PARAMETERS\n")
        f.write("-" * 30 + "\n")
        for key, value in metadata["strategy_parameters"].items():
            f.write(f"{key}: {value}\n")
        f.write(f"Fee Rate: {metadata['fee_rate']*100:.4f}%\n")
        f.write(f"Data Mode: {metadata['data_mode']}\n\n")
        
        f.write("CAPITAL SUMMARY\n")
        f.write("-" * 30 + "\n")
        f.write(f"Initial Capital: ${metadata['initial_capital']:,.2f}\n")
        if metadata.get('leverage', 1) > 1:
            f.write(f"Leverage: {metadata['leverage']:.0f}x\n")
            f.write(f"Effective Margin: ${metadata['effective_margin']:,.2f}\n")
        if trades:
            final_equity = summary.get('final_equity', trades[-1].get("cumulative_pnl", metadata["initial_capital"]))
            f.write(f"Final Capital: ${final_equity:,.2f}\n")
            f.write(f"Net P&L: ${summary.get('net_pnl', 0):,.2f}\n")
            return_pct = summary.get('total_return_pct', summary.get('net_pnl', 0)/metadata['initial_capital']*100)
            f.write(f"Return on Capital: {return_pct:.2f}%\n")
            if summary.get('liquidated'):
                f.write(f"*** LIQUIDATED at trade #{summary['liquidation_trade_index'] + 1} ***\n")
        f.write("\n")
        
        f.write("TRADING STATISTICS\n")
        f.write("-" * 30 + "\n")
        f.write(f"Total Trades: {summary.get('total_trades', 0)}\n")
        f.write(f"Winning Trades: {summary.get('winning_trades', 0)}\n")
        f.write(f"Losing Trades: {summary.get('losing_trades', 0)}\n")
        f.write(f"Win Rate: {summary.get('win_rate_pct', 0):.2f}%\n")
        f.write(f"Profit Factor: {summary.get('profit_factor', 0):.2f}\n")
        f.write(f"Max Drawdown: ${summary.get('max_drawdown', 0):,.2f}\n")
        f.write(f"Average Trade P&L: ${summary.get('average_trade_pnl', 0):,.2f}\n")
        f.write(f"Largest Win: ${summary.get('largest_winning_trade', 0):,.2f}\n")
        f.write(f"Largest Loss: ${summary.get('largest_losing_trade', 0):,.2f}\n")
        f.write(f"Total Fees: ${summary.get('total_fees', 0):,.2f}\n\n")
        
        f.write("TRADE DETAILS\n")
        f.write("-" * 30 + "\n")
        for i, trade in enumerate(trades[:10]):  # Show first 10 trades
            f.write(f"Trade {i+1}: {trade['option_type'].upper()} "
                   f"Strike ${trade['strike']:,.0f} "
                   f"Qty {trade['quantity']} "
                   f"P&L ${trade['net_pnl']:,.2f}\n")
        if len(trades) > 10:
            f.write(f"... and {len(trades) - 10} more trades\n")
            
        f.write("\nEXECUTION INFO\n")
        f.write("-" * 30 + "\n")
        f.write(f"Run at: {metadata['execution_timestamp']}\n")
        f.write(f"Data Range: {metadata['data_range']['start']} to {metadata['data_range']['end']}\n")


def compare_years(years: list, initial_capital: float, cfg, data_mode: str = "reconstructed", leverage: float = 1.0):
    """
    Compare backtest results across multiple years.
    """
    console = Console()
    
    results = {}
    for year in years:
        console.print(f"\n[cyan]Running {year} backtest...[/cyan]")
        result = run_year_backtest(year, initial_capital, cfg, data_mode, leverage)
        results[year] = result
        
    # Display comparison table
    title_suffix = f" | {leverage:.0f}x Leverage" if leverage > 1 else ""
    table = Table(title=f"Year-by-Year Comparison (Capital: ${initial_capital:,.2f}{title_suffix})")
    table.add_column("Year", style="cyan")
    table.add_column("Capital", justify="right")
    if leverage > 1:
        table.add_column("Eff. Margin", justify="right")
    table.add_column("Final", justify="right")
    table.add_column("Net P&L", justify="right")
    table.add_column("Return %", justify="right")
    table.add_column("Win Rate %", justify="right")
    table.add_column("Max DD", justify="right")
    table.add_column("Trades", justify="right")
    if leverage > 1:
        table.add_column("Liquidated?", justify="center")
    
    for year in years:
        if year in results:
            summary = results[year]["summary"]
            
            final_equity = summary.get("final_equity", initial_capital)
            net_pnl = summary.get("net_pnl", 0)
            return_pct = summary.get("total_return_pct", 0)
            liq_status = "💀 YES" if summary.get("liquidated") else "✅ No"
                
            row = [
                str(year),
                f"${initial_capital:,.0f}",
            ]
            if leverage > 1:
                row.append(f"${initial_capital * leverage:,.0f}")
            row.extend([
                f"${final_equity:,.0f}",
                f"${net_pnl:,.0f}",
                f"{return_pct:.1f}%",
                f"{summary.get('win_rate_pct', 0):.1f}%",
                f"${summary.get('max_drawdown', 0):,.0f}",
                str(summary.get('total_trades', 0)),
            ])
            if leverage > 1:
                row.append(liq_status)
            table.add_row(*row)
            
    console.print(table)
    
    # Save comparison
    import json
    comparison = {
        "initial_capital": initial_capital,
        "leverage": leverage,
        "effective_margin": initial_capital * leverage,
        "data_mode": data_mode,
        "years": years,
        "results": {year: results[year]["summary"] for year in years if year in results}
    }
    
    with open("backtest_results/year_comparison.json", "w") as f:
        json.dump(comparison, f, indent=2)


def main():
    import sys
    
    console = Console()
    
    # Parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Specific year to backtest (e.g., 2024)")
    parser.add_argument("--years", type=str, help="Comma-separated years (e.g., '2024,2025,2026')")
    parser.add_argument("--mode", choices=["reconstructed", "realistic"], default="reconstructed",
                        help="Data mode: reconstructed (theoretical) or realistic (historical)")
    parser.add_argument("--capital", type=float, help="Initial capital amount")
    parser.add_argument("--leverage", type=float, default=100.0,
                        help="Leverage multiplier (default: 100x). Your capital * leverage = effective margin used for position sizing.")
    parser.add_argument("--compare", action="store_true", help="Compare multiple years")
    parser.add_argument("--min-premium", type=float, default=None,
                        help="Override MIN_PREMIUM for this run only.")
    args = parser.parse_args()
    
    # Get initial capital interactively if not provided
    initial_capital = args.capital
    if initial_capital is None:
        try:
            capital_input = input("Enter initial capital (e.g., 100000 for ₹100,000 or $100,000): ")
            initial_capital = float(capital_input.replace(",", "").replace("₹", "").replace("$", ""))
        except (ValueError, EOFError):
            console.print("[red]Invalid capital input. Using default: $10,000[/red]")
            initial_capital = 10000.0
            
    leverage = args.leverage
    if leverage < 1:
        console.print("[red]Leverage must be >= 1. Using 1x (no leverage).[/red]")
        leverage = 1.0
    
    # Handle overrides including the margin_budget_usd fixed to initial_capital * leverage
    # With leverage, your effective margin is capital * leverage (e.g. ₹1000 * 100x = ₹100,000)
    effective_margin = initial_capital * leverage
    cfg = CONFIG if args.min_premium is None else replace(CONFIG, min_premium_usd=args.min_premium)
    cfg = replace(cfg, margin_budget_usd=effective_margin)
    
    # Determine which years to backtest
    if args.year:
        years = [args.year]
    elif args.years:
        years = [int(y.strip()) for y in args.years.split(",")]
    elif args.compare:
        # Default years for comparison
        current_year = datetime.datetime.now().year
        years = list(range(current_year - 3, current_year))
        years = [y for y in years if y >= 2020]  # Only recent years
    else:
        # Interactive year selection
        console.print("\nSelect backtest year:")
        console.print("1. 2024")
        console.print("2. 2025") 
        console.print("3. 2026")
        console.print("4. Compare all years")
        console.print("5. Custom year")
        
        try:
            choice = input("Enter choice (1-5): ").strip()
            if choice == "1":
                years = [2024]
            elif choice == "2":
                years = [2025]
            elif choice == "3":
                years = [2026]
            elif choice == "4":
                years = [2024, 2025, 2026]
            elif choice == "5":
                custom_year = int(input("Enter year (e.g., 2024): "))
                years = [custom_year]
            else:
                console.print("[red]Invalid choice. Using 2024.[/red]")
                years = [2024]
        except (ValueError, EOFError):
            console.print("[red]Invalid input. Using 2024.[/red]")
            years = [2024]
    
    console.print(f"\n[green]Initial Capital: ${initial_capital:,.2f}[/green]")
    if leverage > 1:
        console.print(f"[yellow]Leverage: {leverage:.0f}x → Effective Margin: ${effective_margin:,.2f}[/yellow]")
    console.print(f"[green]Selected Years: {years}[/green]")
    
    # Warn about realistic mode
    if args.mode == "realistic":
        console.print("[yellow]WARNING: 'realistic' mode uses historical option data.")
        console.print("Limited historical data may affect results.[/yellow]")
    
    # Run backtests
    if len(years) == 1:
        result = run_year_backtest(years[0], initial_capital, cfg, args.mode, leverage)
        print_beautiful_output(result)
        
        if result["summary"]["total_trades"] == 0:
            max_seen = result["summary"].get("max_skipped_premium_seen")
            if max_seen is not None:
                console.print(
                    f"\n[yellow]No trades completed. The highest premium seen on skipped signals was "
                    f"{max_seen}, below MIN_PREMIUM={cfg.min_premium_usd}. "
                    f"Try lowering --min-premium for reconstructed-mode tests.[/yellow]"
                )
    else:
        compare_years(years, initial_capital, cfg, args.mode, leverage)


if __name__ == "__main__":
    main()
