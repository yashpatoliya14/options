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


def fetch_underlying_candles(start_ts: int, end_ts: int, resolution: str = "3h") -> List[Candle]:
    """
    Pulls real historical underlying candles from Delta (this part IS fully
    realistic regardless of data_mode -- underlying OHLCV history is
    confirmed available). If '3h' isn't a supported native resolution,
    aggregates from '1h' candles, 3-at-a-time, closing only on the 3rd.
    """
    client = DeltaClient(CONFIG)

    def fetch_chunked(res: str) -> List[dict]:
        # Keep requests comfortably below common exchange candle limits
        # (typically 2,000 rows). A 90-day 1h window is 2,160 rows, so use
        # 60-day chunks and deduplicate boundary candles.
        chunk_seconds = 60 * 86400 if res == "1h" else 180 * 86400
        rows = []
        cursor = start_ts
        while cursor < end_ts:
            chunk_end = min(cursor + chunk_seconds, end_ts)
            rows.extend(client.get_historical_candles(
                CONFIG.underlying_symbol, res, cursor, chunk_end
            ))
            cursor = chunk_end + 1
        return sorted({int(r["time"]): r for r in rows}.values(), key=lambda r: int(r["time"]))

    # Some Delta deployments reject 3h as an unsupported resolution. Try it
    # first, then transparently fall back to 1h aggregation.
    try:
        raw = fetch_chunked(resolution)
    except Exception as exc:
        if resolution != "3h":
            raise
        print("Note: Native 3h candles unavailable; successfully falling back to 1h aggregation.")
        raw = []

    if raw:
        return [
            Candle(timestamp=int(r["time"]), open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]))
            for r in raw
        ]

    # Fallback: aggregate 1h -> 3h.
    raw_1h = fetch_chunked("1h")
    candles = []
    for i in range(0, len(raw_1h) - 2, 3):
        chunk = raw_1h[i:i + 3]
        candles.append(Candle(
            timestamp=int(chunk[-1]["time"]),
            open=float(chunk[0]["open"]),
            high=max(float(c["high"]) for c in chunk),
            low=min(float(c["low"]) for c in chunk),
            close=float(chunk[-1]["close"]),
        ))
    return candles


def run_backtest(candles: List[Candle], data_mode: str, cfg, initial_margin: float = 1000.0) -> dict:
    if not candles:
        return summarize([], data_mode, cfg, initial_margin)

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
        engine.on_candle_close(c)

    # Realize any open position at the end of the dataset so PnL isn't left unrealized.
    broker.set_clock(candles[-1].timestamp)
    engine.finalize(candles[-1].timestamp)

    return summarize(events, data_mode, cfg, initial_margin)


def summarize(events: List[EngineEvent], data_mode: str, cfg, initial_margin: float = 1000.0) -> dict:
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
            # selling an option: profit = (entry_premium - exit_premium) * quantity
            # (you collect premium on entry, pay it back to close)
            gross_pnl = (entry_premium - exit_premium) * quantity
            fees = (entry_premium + exit_premium) * quantity * (cfg.fee_pct / 100.0)
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

    equity_curve = []
    running = initial_margin
    for t in trades:
        running += t["net_pnl"]
        t["cumulative_pnl"] = round(running, 2)
        equity_curve.append({"timestamp": t["exit_timestamp"], "equity": round(running, 2)})

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
        month = datetime.datetime.fromtimestamp(t["exit_timestamp"], datetime.UTC).strftime("%Y-%m")
        monthly_pnl[month] = round(monthly_pnl.get(month, 0.0) + t["net_pnl"], 2)

    summary = {
        "data_mode": data_mode,
        "data_mode_warning": (
            None if data_mode == "realistic" else
            "RECONSTRUCTED MODE: option premiums are Black-Scholes theoretical "
            "estimates using an assumed flat implied volatility, not real "
            "historical bid/ask data. Treat P&L figures as directional/illustrative "
            "only, not a reliable estimate of real trading results."
        ),
        "total_trades": total,
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["reconstructed", "realistic"], default=CONFIG.backtest_data_mode
                         if CONFIG.backtest_data_mode in ("reconstructed", "realistic") else "reconstructed")
    parser.add_argument("--min-premium", type=float, default=None,
                        help="Override MIN_PREMIUM for this run only.")
    parser.add_argument("--days", type=int, default=180, help="lookback window if --start/--end not given")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--initial-margin", type=float, default=1000.0, help="Initial margin/capital")
    args = parser.parse_args()

    if args.start and args.end:
        import datetime
        start_ts = int(datetime.datetime.fromisoformat(args.start).timestamp())
        end_ts = int(datetime.datetime.fromisoformat(args.end).timestamp())
    else:
        end_ts = int(time.time())
        start_ts = end_ts - args.days * 86400

    console = Console()
    console.print(f"[cyan]Fetching underlying candles from {start_ts} to {end_ts}...[/cyan]")
    candles = fetch_underlying_candles(start_ts, end_ts)
    console.print(f"[green]Loaded {len(candles)} 3h candles.[/green]")

    cfg = CONFIG if args.min_premium is None else replace(CONFIG, min_premium_usd=args.min_premium)
    console.print(f"[cyan]Using MIN_PREMIUM={cfg.min_premium_usd}[/cyan]")
    console.print(f"[cyan]Initial Margin: {args.initial_margin}[/cyan]")

    if args.mode == "realistic":
        console.print("[yellow]WARNING: 'realistic' mode requires HistoricalOptionDataSource to be "
              "implemented against confirmed-available cached data (see run_diagnostic.py). "
              "It currently raises NotImplementedError until that's wired up.[/yellow]")

    result = run_backtest(candles, args.mode, cfg, args.initial_margin)
    print_beautiful_output(result)

    if result["summary"]["total_trades"] == 0:
        max_seen = result["summary"].get("max_skipped_premium_seen")
        if max_seen is not None:
            console.print(
                f"\n[yellow]No trades completed. The highest premium seen on skipped signals was "
                f"{max_seen}, below MIN_PREMIUM={cfg.min_premium_usd}. "
                f"Try lowering --min-premium for reconstructed-mode tests.[/yellow]"
            )


if __name__ == "__main__":
    main()
