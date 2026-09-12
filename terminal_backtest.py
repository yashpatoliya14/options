"""
Supertrend 1H Options Backtest -- Terminal Dashboard
====================================================
SUPERTREND: 1H | ATR PERIOD: 15 | MULTIPLIER: 1.5

Fetches BTCUSDT 1H candles from Binance, runs a full backtest using
Bull Put Spread (buy signal) / Bear Call Credit Spread (sell signal), and
prints a comprehensive terminal dashboard with all metrics.
"""

import io
import logging
import os
import sys
from time import perf_counter

# Force UTF-8 stdout on Windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
import pandas as pd
from datetime import datetime, timezone

try:
    from colorama import init, Fore, Style
    init()
except ImportError:
    class _NoColor:
        def __getattr__(self, _):
            return ""
    Fore = _NoColor()
    Style = _NoColor()

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from engine import StrategyEngine, StrategyParams
from backtest import BacktestRunner, HistoricalDataProvider, SimulatedClock, SimulatedExecutor


LOGGER = logging.getLogger("terminal_backtest")


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: Fore.CYAN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self.COLORS.get(record.levelno, Fore.WHITE)
        return f"{color}{message}{Style.RESET_ALL}"


def configure_logging() -> None:
    """Configure concise timestamped progress logs for the CLI run."""
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        force=True,
    )


def log_completed_step(step: str, started_at: float, detail: str = "") -> None:
    elapsed = perf_counter() - started_at
    suffix = f" | {detail}" if detail else ""
    LOGGER.info("completed: %s | elapsed=%.2fs%s", step, elapsed, suffix)


# ======================================================================
# DATA FETCHING
# ======================================================================

def fetch_binance_1h_data(symbol: str, max_candles: int = 20000) -> pd.DataFrame:
    """Fetch 1H candles from Binance with local CSV caching."""
    started_at = perf_counter()
    cache_file = f"{symbol}_1h_cache.csv"

    # Load existing cache
    if os.path.exists(cache_file):
        print(f"{Fore.CYAN}  Loading cached data from {cache_file}...{Style.RESET_ALL}")
        LOGGER.info("using local cache: %s", cache_file)
        df_cache = pd.read_csv(cache_file)
        df_cache["timestamp"] = pd.to_datetime(df_cache["timestamp"], utc=True)
        latest_ts = int(df_cache["timestamp"].max().timestamp() * 1000)
    else:
        df_cache = pd.DataFrame()
        latest_ts = None

    print(f"{Fore.CYAN}  Fetching new 1H data for {symbol}...{Style.RESET_ALL}")
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_klines = []
    limit = 1000

    if latest_ts:
        start_time = latest_ts + 1
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
                print(f"    Fetched {len(all_klines)} new candles...", end="\r")
                if len(data) < limit:
                    break
            except Exception as e:
                print(f"{Fore.RED}  Error fetching data: {e}{Style.RESET_ALL}")
                break
    else:
        end_time = None
        while len(all_klines) < max_candles:
            p = {"symbol": symbol, "interval": "1h", "limit": limit}
            if end_time:
                p["endTime"] = end_time
            try:
                response = requests.get(url, params=p)
                response.raise_for_status()
                data = response.json()
                if not data:
                    break
                all_klines.extend(data)
                end_time = data[0][0] - 1
                print(f"    Fetched {len(all_klines)} candles...", end="\r")
            except Exception as e:
                print(f"{Fore.RED}  Error fetching data: {e}{Style.RESET_ALL}")
                break

    print()

    if all_klines:
        df_new = pd.DataFrame(all_klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
        ])
        df_new["timestamp"] = pd.to_datetime(df_new["timestamp"], unit="ms", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df_new[col] = pd.to_numeric(df_new[col], errors="coerce")
        df_new = df_new[["timestamp", "open", "high", "low", "close", "volume"]]
        df = pd.concat([df_cache, df_new], ignore_index=True)
    else:
        df = df_cache

    if df.empty:
        LOGGER.warning("no candles available for %s", symbol)
        return df

    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df.to_csv(cache_file, index=False)
    result = df.tail(max_candles).reset_index(drop=True)
    log_completed_step(
        "market data fetch",
        started_at,
        f"candles={len(result)}; range={result['timestamp'].min()} -> {result['timestamp'].max()}",
    )
    return result


# ======================================================================
# ASCII EQUITY CURVE
# ======================================================================

def render_equity_curve(equity: list[float], width: int = 70, height: int = 15) -> str:
    """Render an ASCII equity curve for the terminal."""
    if not equity or len(equity) < 2:
        return "  (not enough data for equity curve)"

    # Downsample if needed
    if len(equity) > width:
        step = len(equity) / width
        sampled = [equity[int(i * step)] for i in range(width)]
    else:
        sampled = equity

    mn = min(sampled)
    mx = max(sampled)
    spread = mx - mn if mx != mn else 1.0

    lines = []
    for row in range(height, -1, -1):
        threshold = mn + (row / height) * spread
        line_chars = []
        for val in sampled:
            if val >= threshold:
                line_chars.append("#")
            else:
                line_chars.append(" ")
        # Y-axis label
        if row == height:
            label = f"${mx:>10,.0f} |"
        elif row == height // 2:
            mid = mn + spread / 2
            label = f"${mid:>10,.0f} |"
        elif row == 0:
            label = f"${mn:>10,.0f} |"
        else:
            label = "            |"
        lines.append(f"  {label}{''.join(line_chars)}")

    # X-axis
    lines.append("  " + "            +" + "-" * len(sampled))
    lines.append("  " + "             Start" + " " * max(0, len(sampled) - 12) + "End")

    return "\n".join(lines)


# ======================================================================
# DASHBOARD
# ======================================================================

def fmt_pnl(val: float) -> str:
    """Format a P&L value with color."""
    color = Fore.GREEN if val >= 0 else Fore.RED
    return f"{color}${val:>+,.2f}{Style.RESET_ALL}"


def fmt_pct(val: float) -> str:
    """Format a percentage value with color."""
    color = Fore.GREEN if val >= 0 else Fore.RED
    return f"{color}{val:>+.2f}%{Style.RESET_ALL}"


def fmt_ratio(val: float) -> str:
    """Format a ratio with color."""
    if val == float("inf"):
        return f"{Fore.GREEN}inf{Style.RESET_ALL}"
    color = Fore.GREEN if val >= 1.0 else Fore.RED
    return f"{color}{val:.2f}{Style.RESET_ALL}"


def print_section(title: str):
    """Print a section header."""
    line = "=" * 72
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{line}{Style.RESET_ALL}")
    print(f"  {Fore.CYAN}{Style.BRIGHT}{title}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.DIM}{'-' * 72}{Style.RESET_ALL}")


def print_badge(label: str, value: str, color: str = Fore.WHITE) -> None:
    print(f"  {Fore.WHITE}{label:<24}{Style.RESET_ALL}{color}{Style.BRIGHT}{value}{Style.RESET_ALL}")


def print_strategy_panel(params: StrategyParams, symbol: str) -> None:
    """Show the active strategy contract before performance metrics."""
    print_section("ACTIVE STRATEGY")
    print_badge("Market", f"{symbol} | {params.resolution}", Fore.CYAN)
    print_badge("Signal", "Supertrend + EMA filter", Fore.YELLOW)
    print_badge("Bull structure", "Bull put credit", Fore.GREEN)
    print_badge("Bear structure", "Bear call credit", Fore.MAGENTA)
    print_badge("Target credit / loss", f"{params.target_credit_risk_ratio:.2f}:1", Fore.GREEN)
    print_badge("Stop loss", "DISABLED", Fore.YELLOW)
    print_badge("Reversal exit", f"after {params.reversal_profit_capture_pct:.0%} capture", Fore.CYAN)
    print_badge("Expiry hold", "ENABLED", Fore.GREEN)


def print_dashboard(result, symbol: str, params: StrategyParams, candle_count: int | None = None):
    """Print the full terminal backtest dashboard."""
    rep = result.report
    trades = result.trades

    # -- Header --
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}{'=' * 72}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}  BTC OPTIONS | SUPERTREND BACKTEST CONSOLE{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.DIM}  1H candles | ATR {params.supertrend_atr_period} x {params.supertrend_multiplier:.1f} | reconstructed options{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}{'=' * 72}{Style.RESET_ALL}")
    print_strategy_panel(params, symbol)

    result_color = Fore.GREEN if rep["total_pnl"] >= 0 and rep["trade_count"] > 0 else Fore.YELLOW
    result_label = "PROFITABLE RUN" if rep["total_pnl"] > 0 else "NO PROFIT"
    if rep["trade_count"] == 0:
        result_label = "NO QUALIFYING TRADES"
    print_section("RUN STATUS")
    print_badge("Result", result_label, result_color)
    print_badge("Data mode", rep["data_mode"].upper(), Fore.CYAN)
    displayed_candles = f"{candle_count:,}" if candle_count is not None else "available"
    print_badge("Candles", displayed_candles, Fore.WHITE)

    # -- Performance Summary --
    print_section("PERFORMANCE SUMMARY")
    print(f"    Starting Capital:        {Fore.WHITE}${rep['starting_capital']:>12,.2f}{Style.RESET_ALL}")
    print(f"    Ending Capital:          {Fore.WHITE}${rep['ending_capital']:>12,.2f}{Style.RESET_ALL}")
    print(f"    Net P&L:                 {fmt_pnl(rep['total_pnl'])}")
    print(f"    Total Return:            {fmt_pct(rep['total_return_pct'])}")
    print(f"    CAGR:                    {fmt_pct(rep['cagr'])}")

    # -- Trade Statistics --
    print_section("TRADE STATISTICS")
    print(f"    Total Trades:            {Fore.WHITE}{rep['trade_count']:>6}{Style.RESET_ALL}")
    print(f"    Winning Trades:          {Fore.GREEN}{rep['win_count']:>6}{Style.RESET_ALL}")
    print(f"    Losing Trades:           {Fore.RED}{rep['loss_count']:>6}{Style.RESET_ALL}")
    print(f"    Win Rate:                {Fore.WHITE}{rep['win_rate']*100:.2f}%{Style.RESET_ALL}")
    print(f"    Profit Factor:           {fmt_ratio(rep['profit_factor'])}")
    print(f"    Average Trade P&L:       {fmt_pnl(rep['avg_trade_pnl'])}")
    print(f"    Average Winner:          {fmt_pnl(rep['avg_win'])}")
    print(f"    Average Loser:           {fmt_pnl(rep['avg_loss'])}")

    # -- Risk Metrics --
    print_section("RISK METRICS")
    print(f"    Max Drawdown $:          {Fore.RED}${rep['max_drawdown_dollar']:>+,.2f}{Style.RESET_ALL}")
    print(f"    Max Drawdown %:          {Fore.RED}{rep['max_drawdown_pct']:.2f}%{Style.RESET_ALL}")
    print(f"    Sharpe Ratio:            {fmt_ratio(rep['sharpe_ratio'])}")
    print(f"    Sortino Ratio:           {fmt_ratio(rep['sortino_ratio'])}")
    print(f"    Max Consecutive Losses:  {Fore.RED}{rep['max_consecutive_losses']}{Style.RESET_ALL}")

    # -- Timing & Costs --
    print_section("TIMING & COSTS")
    avg_dur = rep["avg_trade_duration_min"]
    if avg_dur >= 1440:
        dur_str = f"{avg_dur / 1440:.1f} days"
    elif avg_dur >= 60:
        dur_str = f"{avg_dur / 60:.1f} hours"
    else:
        dur_str = f"{avg_dur:.1f} min"
    print(f"    Avg Trade Duration:      {Fore.WHITE}{dur_str}{Style.RESET_ALL}")
    print(f"    Total Fees:              {Fore.WHITE}${rep['total_fees']:>10,.2f}{Style.RESET_ALL}")
    print(f"    Total Slippage:          {Fore.WHITE}${rep['total_slippage']:>10,.2f}{Style.RESET_ALL}")
    print(f"    Skipped (No Credit):     {Fore.WHITE}{rep['skipped_no_valid_credit']}{Style.RESET_ALL}")
    print(f"    Skipped (Cooldown):      {Fore.WHITE}{rep['skipped_cooldown']}{Style.RESET_ALL}")

    # -- Separate Bull / Bear Performance --
    print_section("BULL PUT SPREAD PERFORMANCE (Buy Signals)")
    bps = rep["bull_put_spread"]
    print(f"    Trades:                  {Fore.WHITE}{bps['trades']}{Style.RESET_ALL}")
    print(f"    Win Rate:                {Fore.WHITE}{bps['win_rate']*100:.2f}%{Style.RESET_ALL}")
    print(f"    Net P&L:                 {fmt_pnl(bps['net_pnl'])}")
    print(f"    Profit Factor:           {fmt_ratio(bps['profit_factor'])}")
    print(f"    Average P&L:             {fmt_pnl(bps['avg_pnl'])}")

    print_section("BEAR CALL CREDIT SPREAD PERFORMANCE (Sell Signals)")
    brps = rep["bear_put_spread"]
    print(f"    Trades:                  {Fore.WHITE}{brps['trades']}{Style.RESET_ALL}")
    print(f"    Win Rate:                {Fore.WHITE}{brps['win_rate']*100:.2f}%{Style.RESET_ALL}")
    print(f"    Net P&L:                 {fmt_pnl(brps['net_pnl'])}")
    print(f"    Profit Factor:           {fmt_ratio(brps['profit_factor'])}")
    print(f"    Average P&L:             {fmt_pnl(brps['avg_pnl'])}")

    # -- Equity Curve --
    print_section("EQUITY CURVE")
    equity = rep.get("equity_curve", [])
    if equity:
        print(render_equity_curve(equity))
    else:
        print("    (no trades to plot)")

    print(f"\n  {Fore.WHITE}Full trade log saved to pnl_report.md{Style.RESET_ALL}")
    print(f"\n{Fore.YELLOW}{Style.BRIGHT}{'=' * 72}{Style.RESET_ALL}\n")


# ======================================================================
# MARKDOWN P&L REPORT
# ======================================================================

def write_pnl_report(result, symbol: str, params: StrategyParams, filepath: str = "pnl_report.md"):
    """Write the full P&L report as a markdown file."""
    rep = result.report
    trades = result.trades

    def _pnl(v):
        return f"${v:+,.2f}"

    def _pct(v):
        return f"{v:+.2f}%"

    def _ratio(v):
        return "inf" if v == float("inf") else f"{v:.2f}"

    lines = []
    w = lines.append

    w(f"# P&L Report - {symbol} Supertrend Options Backtest")
    w("")
    w(f"> **SUPERTREND: 1H | ATR PERIOD: {params.supertrend_atr_period} | MULTIPLIER: {params.supertrend_multiplier}**")
    w("")
    w(f"- **Asset:** {symbol}")
    w(f"- **Data Mode:** {rep['data_mode'].upper()}")
    w(f"- **Spread Type:** Directional (Bull Put / Bear Call Credit)")
    w(f"- **Timeframe:** {params.resolution}")
    w(f"- **Slippage:** {params.slippage_pct*100:.2f}%")
    w(f"- **Commission/leg:** ${params.commission_per_leg:.2f}")
    w("")
    w("---")
    w("")

    # Performance Summary
    w("## Performance Summary")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Starting Capital | ${rep['starting_capital']:,.2f} |")
    w(f"| Ending Capital | ${rep['ending_capital']:,.2f} |")
    w(f"| Net P&L | {_pnl(rep['total_pnl'])} |")
    w(f"| Total Return | {_pct(rep['total_return_pct'])} |")
    w(f"| CAGR | {_pct(rep['cagr'])} |")
    w("")

    # Trade Statistics
    w("## Trade Statistics")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Total Trades | {rep['trade_count']} |")
    w(f"| Winning Trades | {rep['win_count']} |")
    w(f"| Losing Trades | {rep['loss_count']} |")
    w(f"| Win Rate | {rep['win_rate']*100:.2f}% |")
    w(f"| Profit Factor | {_ratio(rep['profit_factor'])} |")
    w(f"| Average Trade P&L | {_pnl(rep['avg_trade_pnl'])} |")
    w(f"| Average Winner | {_pnl(rep['avg_win'])} |")
    w(f"| Average Loser | {_pnl(rep['avg_loss'])} |")
    w("")

    # Risk Metrics
    w("## Risk Metrics")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Max Drawdown $ | {_pnl(rep['max_drawdown_dollar'])} |")
    w(f"| Max Drawdown % | {rep['max_drawdown_pct']:.2f}% |")
    w(f"| Sharpe Ratio | {_ratio(rep['sharpe_ratio'])} |")
    w(f"| Sortino Ratio | {_ratio(rep['sortino_ratio'])} |")
    w(f"| Max Consecutive Losses | {rep['max_consecutive_losses']} |")
    w("")

    # Timing & Costs
    avg_dur = rep["avg_trade_duration_min"]
    if avg_dur >= 1440:
        dur_str = f"{avg_dur / 1440:.1f} days"
    elif avg_dur >= 60:
        dur_str = f"{avg_dur / 60:.1f} hours"
    else:
        dur_str = f"{avg_dur:.1f} min"

    w("## Timing & Costs")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Avg Trade Duration | {dur_str} |")
    w(f"| Total Fees | ${rep['total_fees']:,.2f} |")
    w(f"| Total Slippage | ${rep['total_slippage']:,.2f} |")
    w(f"| Skipped (No Credit) | {rep['skipped_no_valid_credit']} |")
    w(f"| Skipped (Cooldown) | {rep['skipped_cooldown']} |")
    w("")

    # Bull / Bear Breakdown
    w("## Bull Put Spread Performance (Buy Signals)")
    w("")
    bps = rep["bull_put_spread"]
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Trades | {bps['trades']} |")
    w(f"| Win Rate | {bps['win_rate']*100:.2f}% |")
    w(f"| Net P&L | {_pnl(bps['net_pnl'])} |")
    w(f"| Profit Factor | {_ratio(bps['profit_factor'])} |")
    w(f"| Average P&L | {_pnl(bps['avg_pnl'])} |")
    w("")

    w("## Bear Put Credit Spread Performance (Sell Signals)")
    w("")
    brps = rep["bear_put_spread"]
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Trades | {brps['trades']} |")
    w(f"| Win Rate | {brps['win_rate']*100:.2f}% |")
    w(f"| Net P&L | {_pnl(brps['net_pnl'])} |")
    w(f"| Profit Factor | {_ratio(brps['profit_factor'])} |")
    w(f"| Average P&L | {_pnl(brps['avg_pnl'])} |")
    w("")

    # Trade Log
    w("---")
    w("")
    w("## Trade Log")
    w("")
    w("| # | Signal | Entry Time | Expiry | Underlying | Short Put | Long Put | Credit/Debit | Max Profit | Max Loss | Exit Time | Exit Reason | P&L |")
    w("|--:|:------:|:----------:|:------:|----------:|--------:|-------:|-----------:|---------:|-------:|:---------:|:-----------:|---:|")

    for idx, t in enumerate(trades, 1):
        sig = "BUY" if t.signal_type == "buy" else "SELL"
        entry = t.entry_time.strftime("%Y-%m-%d %H:%M")
        exit_t = t.exit_time.strftime("%Y-%m-%d %H:%M")
        cr_dr = f"CR {t.credit_received:.1f}" if t.credit_received > 0 else f"DR {abs(t.credit_received):.1f}"
        pnl = f"${t.realized_pnl:+.2f}"

        w(
            f"| {idx} | {sig} | {entry} | {t.expiry} | "
            f"{t.underlying_price:,.0f} | {t.short_strike:,.0f} | {t.long_strike:,.0f} | "
            f"{cr_dr} | {t.max_profit:.1f} | {t.max_loss:.1f} | "
            f"{exit_t} | {t.exit_reason} | {pnl} |"
        )

    w("")
    w(f"**Total: {len(trades)} trades**")
    w("")

    # Write file
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return report_path


# ======================================================================
# MAIN
# ======================================================================

def main():
    configure_logging()
    run_started_at = perf_counter()
    SYMBOL = "BTCUSDT"
    try:
        trade_qty = int(os.getenv("TRADE_QTY", "1"))
    except ValueError:
        LOGGER.error("invalid TRADE_QTY; use a positive integer")
        return
    if trade_qty <= 0:
        LOGGER.error("TRADE_QTY must be greater than zero")
        return

    print(f"\n{Fore.YELLOW}{Style.BRIGHT}{'=' * 72}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}  SUPERTREND OPTIONS BACKTEST ENGINE{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}  SUPERTREND: 1H  |  ATR PERIOD: 15  |  MULTIPLIER: 1.5{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}{Style.BRIGHT}{'=' * 72}{Style.RESET_ALL}")

    # 1. Fetch 1H data
    print(f"\n{Fore.CYAN}{Style.BRIGHT}[1/3] Fetching Data{Style.RESET_ALL}")
    df = fetch_binance_1h_data(SYMBOL, max_candles=20000)
    if df.empty:
        print(f"{Fore.RED}  ERROR: No data fetched. Cannot run backtest.{Style.RESET_ALL}")
        LOGGER.error("backtest stopped: market data is empty")
        return

    n_candles = len(df)
    date_start = df["timestamp"].iloc[0].strftime("%Y-%m-%d")
    date_end = df["timestamp"].iloc[-1].strftime("%Y-%m-%d")
    print(f"  {Fore.GREEN}OK: {n_candles} candles loaded ({date_start} -> {date_end}){Style.RESET_ALL}")

    # 2. Configure strategy -- Supertrend 1H, ATR 15, Multiplier 1.5
    print(f"\n{Fore.CYAN}{Style.BRIGHT}[2/3] Initializing Engine{Style.RESET_ALL}")
    params = StrategyParams(
        underlying="BTC",
        candle_symbol=SYMBOL,
        resolution="1h",
        spread_type="directional",
        bear_structure="call_credit",

        # -- Supertrend (locked to spec) --
        supertrend_atr_period=15,
        supertrend_multiplier=1.5,
        supertrend_timeframe="1h",
        signal_requires_close=True,

        # -- Trend filters (anti-whipsaw) --
        trend_filter_enabled=True,
        trend_filter_period=50,
        min_trend_bars=2,

        # -- Expiry selection --
        expiry_selection="nearest_valid_after_signal",
        strike_selection="atm_or_nearest_otm",
        expiry_cutoff_hour=9,
        min_credit_risk_ratio=0.00,
        target_credit_risk_ratio=1.00,

        # -- Risk / Exit --
        tp_pct=0.50,
        stop_loss_enabled=False,
        sl_pct=2.00,
        stop_loss_pct=2.00,
        take_profit_pct=0.50,
        exit_on_opposite_signal=True,
        reversal_profit_capture_pct=0.50,
        cooldown_seconds=21600,       # 6 hours
        early_exit_minutes=0,          # hold until expiry unless target/reversal exits

        # -- Sizing & Costs --
        capital=10000.0,
        qty=trade_qty,
        slippage_pct=0.0025,
        commission_per_leg=0.50,
        spread_width=200,

        # -- Black-Scholes --
        option_data_mode="reconstructed",
        assumed_iv=0.55,
        risk_free_rate=0.0,
    )

    clock = SimulatedClock(df["timestamp"].iloc[0].to_pydatetime())
    provider = HistoricalDataProvider(df, clock, params)
    executor = SimulatedExecutor(provider, params)
    engine = StrategyEngine(params)

    runner = BacktestRunner(engine, provider, executor, params, lookback=100)
    print(f"  {Fore.GREEN}OK: Engine ready{Style.RESET_ALL}")
    print(f"    Supertrend ATR Period:  {params.supertrend_atr_period}")
    print(f"    Supertrend Multiplier:  {params.supertrend_multiplier}")
    print(f"    Timeframe:              {params.resolution}")
    print(f"    Trend Filter:           EMA {params.trend_filter_period} ({'ON' if params.trend_filter_enabled else 'OFF'})")
    print(f"    Min Trend Bars:         {params.min_trend_bars}")
    print(f"    Signal Cut:             {'ON' if params.exit_on_opposite_signal else 'OFF'}")
    print(f"    Cooldown:               {params.cooldown_seconds // 3600}h")
    print(f"    Starting Capital:       ${params.capital:,.2f}")
    print(f"    Slippage:               {params.slippage_pct*100:.2f}%")
    print(f"    Commission/leg:         ${params.commission_per_leg:.2f}")
    LOGGER.info(
        "strategy ready: symbol=%s; timeframe=%s; signal=supertrend; spread=directional",
        SYMBOL,
        params.resolution,
    )

    # 3. Run backtest
    backtest_started_at = perf_counter()
    print(f"\n{Fore.CYAN}{Style.BRIGHT}[3/3] Running Backtest{Style.RESET_ALL}")
    result = runner.run()
    print(f"  {Fore.GREEN}OK: Backtest complete -- {len(result.trades)} trades{Style.RESET_ALL}")
    log_completed_step(
        "backtest simulation",
        backtest_started_at,
        f"trades={len(result.trades)}; pnl=${result.report['total_pnl']:+,.2f}",
    )

    # 4. Print dashboard
    print_dashboard(result, "BTC", params, candle_count=n_candles)

    # 5. Write markdown P&L report
    report_path = write_pnl_report(result, "BTC", params)
    print(f"  {Fore.GREEN}OK: P&L report saved to {report_path}{Style.RESET_ALL}\n")
    log_completed_step("P&L report generation", run_started_at, f"file={report_path}")
    LOGGER.info(
        "run complete: trades=%d; ending_capital=$%s; total_elapsed=%.2fs",
        len(result.trades),
        f"{result.report['ending_capital']:,.2f}",
        perf_counter() - run_started_at,
    )


if __name__ == "__main__":
    main()

