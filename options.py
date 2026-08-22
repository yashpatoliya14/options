"""
BTC Options Arbitrage Backtester - Delta Exchange
==================================================
- Conversion / Reversal (Put-Call Parity) arbitrage
- Box Spread detection
- Initial Margin: Rs.1,000
- Rich colorful charts + Month-on-Month PnL
- Realistic slippage + Delta Exchange fee model
- Data: Delta Exchange API with fallback to simulation
"""

import sys
import os

# Force UTF-8 output on Windows (prevents cp1252 encoding errors)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.colors import LinearSegmentedColormap
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

# ===================================================================
#  CONFIGURATION
# ===================================================================
INITIAL_MARGIN_INR = 1000
USD_TO_INR = 83.50
INITIAL_MARGIN_USD = INITIAL_MARGIN_INR / USD_TO_INR
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
STRIKE = 65000
UNDERLYING = "BTC"

# Contract sizing: Delta allows 0.001 BTC minimum
# With 10x leverage on Rs.1000 margin, we can trade ~0.001 BTC contracts
CONTRACT_SIZE_BTC = 0.001   # 0.001 BTC per contract
LEVERAGE = 10               # Delta Exchange max leverage for options

# ===================================================================
#  DELTA EXCHANGE FEE SCHEDULE (as of 2024-2025)
#  https://www.delta.exchange/fees
# ===================================================================
TAKER_FEE_OPTION_BPS  = 3.0    # 0.03% (3 bps) per option leg (on underlying)
MAKER_FEE_OPTION_BPS  = 3.0    # 0.03% maker
TAKER_FEE_FUTURE_BPS  = 5.0    # 0.05% (5 bps) per future leg
SETTLEMENT_FEE_BPS    = 1.5    # 0.015% settlement on ITM at expiry

# ===================================================================
#  SLIPPAGE MODEL (in USD per contract, flat estimate)
#  Real BTC option bid-ask on Delta: $20-100 spread
# ===================================================================
SLIPPAGE_USD_OPTION   = 3.0    # $3 slippage per option fill per 1 BTC
SLIPPAGE_USD_FUTURE   = 1.0    # $1 slippage per future fill per 1 BTC

# ===================================================================
#  PREMIUM COLOR PALETTE (Dark Theme)
# ===================================================================
C = {
    "bg":       "#0d1117",
    "card":     "#161b22",
    "surface":  "#1c2128",
    "text":     "#e6edf3",
    "dim":      "#8b949e",
    "green":    "#3fb950",
    "green2":   "#2ea043",
    "red":      "#f85149",
    "red2":     "#da3633",
    "blue":     "#58a6ff",
    "blue2":    "#1f6feb",
    "purple":   "#bc8cff",
    "purple2":  "#8957e5",
    "orange":   "#d29922",
    "cyan":     "#39d2c0",
    "yellow":   "#e3b341",
    "pink":     "#f778ba",
    "grid":     "#21262d",
    "border":   "#30363d",
}


def setup_ax(ax, title="", xlabel="", ylabel=""):
    """Apply premium dark styling to an axes."""
    ax.set_facecolor(C["card"])
    ax.set_title(title, fontsize=13, fontweight="bold", color=C["text"], pad=12)
    ax.set_xlabel(xlabel, fontsize=10, color=C["dim"])
    ax.set_ylabel(ylabel, fontsize=10, color=C["dim"])
    ax.tick_params(colors=C["dim"], labelsize=9)
    ax.grid(True, alpha=0.15, color=C["grid"], linestyle="--")
    for spine in ax.spines.values():
        spine.set_color(C["border"])
        spine.set_linewidth(0.5)


# ===================================================================
#  DATA FETCHING - Delta Exchange API with fallback
# ===================================================================
def fetch_delta_data():
    """
    Attempt to fetch real BTC options data from Delta Exchange API.
    Returns (call_df, put_df, future_df, strike, data_source_label)
    Falls back to None on any failure.
    """
    try:
        import requests
        BASE = "https://api.delta.exchange/v2"

        print("[*] Connecting to Delta Exchange API...")
        resp = requests.get(
            f"{BASE}/products",
            params={"contract_types": "call_options,put_options,futures,perpetual_futures"},
            timeout=10,
        )
        resp.raise_for_status()
        products = resp.json().get("result", [])

        # Filter BTC chain
        calls, puts, futures = [], [], []
        for p in products:
            sym = p.get("underlying_asset", {}).get("symbol", "")
            if sym != UNDERLYING:
                continue
            ct = p.get("contract_type", "")
            state = p.get("state", "")
            if state != "live":
                continue
            if ct == "call_options":
                calls.append(p)
            elif ct == "put_options":
                puts.append(p)
            elif ct in ("futures", "perpetual_futures"):
                futures.append(p)

        print(f"    Found: {len(calls)} calls, {len(puts)} puts, {len(futures)} futures (live)")

        if not calls or not puts or not futures:
            raise ValueError("No live BTC options/futures found on Delta Exchange")

        # Find a suitable strike
        available_strikes = sorted(
            set(int(float(c.get("strike_price", 0))) for c in calls if c.get("strike_price"))
        )
        if not available_strikes:
            raise ValueError("No strikes found")

        # Pick a mid-range strike
        strike = min(available_strikes, key=lambda s: abs(s - STRIKE))
        print(f"    Selected strike: {strike}")

        # Find matching symbols
        call_sym = next(
            (p["symbol"] for p in calls
             if int(float(p.get("strike_price", 0))) == strike),
            None,
        )
        put_sym = next(
            (p["symbol"] for p in puts
             if int(float(p.get("strike_price", 0))) == strike),
            None,
        )
        # Prefer BTCUSDT perpetual
        fut_sym = None
        for f in futures:
            if "BTCUSDT" in f.get("symbol", ""):
                fut_sym = f["symbol"]
                break
        if not fut_sym:
            fut_sym = futures[0]["symbol"] if futures else None

        if not all([call_sym, put_sym, fut_sym]):
            raise ValueError(f"Missing symbols for strike {strike}: c={call_sym}, p={put_sym}, f={fut_sym}")

        end = datetime.utcnow()
        start = end - timedelta(days=90)

        print(f"    Fetching 90-day candles...")
        print(f"    Call: {call_sym}")
        print(f"    Put:  {put_sym}")
        print(f"    Fut:  {fut_sym}")

        def _candles(sym, label):
            try:
                r = requests.get(f"{BASE}/history/candles", params={
                    "symbol": sym, "resolution": "5m",
                    "start": int(start.timestamp()),
                    "end": int(end.timestamp()),
                }, timeout=15)
                r.raise_for_status()
                data = r.json().get("result", [])
                df = pd.DataFrame(data)
                if not df.empty:
                    df["time"] = pd.to_datetime(df["time"], unit="s")
                    df = df.sort_values("time").reset_index(drop=True)
                print(f"    {label}: {len(df)} bars")
                return df
            except Exception as e:
                print(f"    {label}: FAILED ({e})")
                return pd.DataFrame()

        call_df = _candles(call_sym, "Call")
        put_df  = _candles(put_sym,  "Put")
        future_df = _candles(fut_sym, "Future")

        # Check we got enough data
        min_bars = min(len(call_df), len(put_df), len(future_df))
        if min_bars < 50:
            raise ValueError(f"Too few bars ({min_bars}). Options may be illiquid or expired.")

        print(f"[OK] Fetched {min_bars}+ bars from Delta Exchange API (LIVE DATA)")
        return call_df, put_df, future_df, strike, "DELTA EXCHANGE API (LIVE)"

    except Exception as e:
        print(f"[!] Delta API data unavailable: {e}")
        print("[*] Falling back to realistic simulated data (90 days)...\n")
        return None, None, None, None, None


def generate_simulated_data(months=3):
    """
    Generate 3 months of realistic BTC option/future simulated data.
    Noise calibrated to real BTC option market microstructure:
    - Option bid-ask spreads on Delta: $20-100 (we model $40 avg half-spread)
    - Future bid-ask: ~$5-10 (tight)
    - Independent noise per leg creates realistic parity gaps
    """
    np.random.seed(42)
    n = months * 30 * 24 * 12  # 5-min bars
    times = pd.date_range("2026-06-01", periods=n, freq="5min")

    # Realistic BTC random walk with volatility clustering
    btc = np.zeros(n)
    btc[0] = 65000
    for i in range(1, n):
        local_vol = 0.0008 * (1 + 0.3 * abs(np.random.normal()))
        btc[i] = btc[i - 1] * np.exp(0.000001 + local_vol * np.random.normal())

    strike = STRIKE
    tte_days = np.arange(n) / (24 * 12)
    time_to_expiry = np.maximum(30 - (tte_days % 30), 0.01) / 365

    # Approximate option pricing (intrinsic + time value)
    call_theo = np.maximum(btc - strike, 0) + 400 * np.sqrt(time_to_expiry)
    put_theo  = np.maximum(strike - btc, 0) + 400 * np.sqrt(time_to_expiry)

    # Realistic market microstructure noise
    # BTC options have wide spreads ($20-100); half-spread noise ~$40
    # This creates parity gaps that sometimes exceed transaction costs
    call_noise = np.random.normal(0, 40, n)  # $40 std dev per option
    put_noise  = np.random.normal(0, 40, n)  # independent noise
    fut_noise  = np.random.normal(0, 8, n)   # futures are tighter

    # Add occasional larger dislocations (market events, illiquidity)
    # ~5% of bars have extra noise (stale quotes, wide spreads)
    dislocation_mask = np.random.random(n) < 0.05
    call_noise[dislocation_mask] += np.random.normal(0, 80, dislocation_mask.sum())
    put_noise[dislocation_mask]  += np.random.normal(0, 80, dislocation_mask.sum())

    call_price   = np.maximum(call_theo + call_noise, 1)
    put_price    = np.maximum(put_theo + put_noise, 1)
    future_price = btc + fut_noise

    call_df = pd.DataFrame({"time": times, "close": call_price})
    put_df  = pd.DataFrame({"time": times, "close": put_price})
    future_df = pd.DataFrame({"time": times, "close": future_price})

    return call_df, put_df, future_df, strike


# ===================================================================
#  BACKTEST ENGINE with Slippage + Charges
# ===================================================================
def backtest_with_costs(call_df, put_df, future_df, strike, notional=1):
    """
    Full backtest with Delta Exchange fee model + slippage.

    Fee model per trade (3-leg arb):
      - 2 option legs x taker fee (3 bps on underlying notional each)
      - 1 future leg  x taker fee (5 bps on underlying notional)
      - Slippage: flat USD per leg (calibrated to real BTC option books)

    All PnL is scaled by CONTRACT_SIZE_BTC to match the margin.
    E.g., with 0.001 BTC contract, a $100 edge = $0.10 actual profit.

    Returns: (all_bars_df, tradable_signals_df, summary_dict)
    """
    # Align on time
    df = call_df[["time"]].copy()
    df["call"] = call_df["close"].values
    df["put"]  = put_df["close"].values
    df["future"] = future_df["close"].values

    # Put-Call Parity gap per 1 BTC: (C - P) - (F - K)
    df["raw_gap"] = (df["call"] - df["put"]) - (df["future"] - strike)

    # ----- Fee calculation per 1 BTC notional -----
    ref_price = df["future"].abs()  # underlying price for bps calc
    # Delta charges bps on underlying notional
    df["fee_option_legs"] = ref_price * (TAKER_FEE_OPTION_BPS / 1e4) * 2  # 2 option legs
    df["fee_future_leg"]  = ref_price * (TAKER_FEE_FUTURE_BPS / 1e4) * 1  # 1 future leg
    df["total_fees"]      = df["fee_option_legs"] + df["fee_future_leg"]

    # ----- Slippage (flat USD per 1 BTC) -----
    df["slip_option_legs"] = SLIPPAGE_USD_OPTION * 2  # 2 option legs
    df["slip_future_leg"]  = SLIPPAGE_USD_FUTURE * 1  # 1 future leg
    df["total_slippage"]   = df["slip_option_legs"] + df["slip_future_leg"]

    # ----- Total cost per 1 BTC -----
    df["total_cost"] = df["total_fees"] + df["total_slippage"]

    # ----- Net edge per 1 BTC -----
    df["gross_edge"]  = df["raw_gap"].abs()
    df["net_edge"]    = df["gross_edge"] - df["total_cost"]
    df["tradable"]    = df["net_edge"] > 0

    # Strategy label
    df["strategy"] = np.where(
        df["raw_gap"] > 0, "reversal",
        np.where(df["raw_gap"] < 0, "conversion", "flat"),
    )

    # Scale everything to actual contract size
    for col in ["raw_gap", "gross_edge", "net_edge", "total_fees",
                "total_slippage", "total_cost", "fee_option_legs",
                "fee_future_leg", "slip_option_legs", "slip_future_leg"]:
        df[col] = df[col] * CONTRACT_SIZE_BTC

    # Filter tradable signals
    trades = df[df["tradable"]].copy()

    # Summary
    summary = {
        "total_bars_scanned": len(df),
        "signals_found": len(trades),
        "signal_rate_pct": round(100 * len(trades) / max(len(df), 1), 3),
        "contract_size_btc": CONTRACT_SIZE_BTC,
        "avg_gross_edge_usd": round(trades["gross_edge"].mean(), 6) if len(trades) else 0,
        "avg_total_fees_usd": round(trades["total_fees"].mean(), 6) if len(trades) else 0,
        "avg_slippage_usd": round(trades["total_slippage"].mean(), 6) if len(trades) else 0,
        "avg_net_edge_usd": round(trades["net_edge"].mean(), 6) if len(trades) else 0,
        "total_fees_paid_usd": round(trades["total_fees"].sum(), 4) if len(trades) else 0,
        "total_slippage_usd": round(trades["total_slippage"].sum(), 4) if len(trades) else 0,
    }

    return df, trades, summary


def detect_box_spread(call_k1, call_k2, put_k1, put_k2, k1, k2, notional=1):
    """Evaluate a single box-spread snapshot with Delta Exchange fees + slippage."""
    fair_value = k2 - k1
    box_value  = (call_k1 - call_k2) + (put_k2 - put_k1)

    ref_price = (k1 + k2) / 2
    # 4-leg trade: all options
    total_fee  = ref_price * (TAKER_FEE_OPTION_BPS / 1e4) * 4 * notional
    total_slip = SLIPPAGE_USD_OPTION * 4 * notional
    total_cost = total_fee + total_slip

    edge     = box_value - fair_value
    net_edge = abs(edge) - total_cost

    direction = "sell_box" if edge > 0 else ("buy_box" if edge < 0 else "flat")

    return {
        "k1": k1,
        "k2": k2,
        "fair_value": fair_value,
        "box_value": round(box_value, 4),
        "raw_edge": round(edge, 4),
        "total_fees": round(total_fee, 4),
        "total_slippage": round(total_slip, 4),
        "total_cost": round(total_cost, 4),
        "net_edge": round(net_edge, 4),
        "direction": direction,
        "notional_pnl": round(net_edge * notional, 4) if net_edge > 0 else 0,
    }


# ===================================================================
#  PORTFOLIO + MONTHLY PnL
# ===================================================================
def compute_portfolio(trades, initial_margin_inr=INITIAL_MARGIN_INR):
    """Compute equity curve with INR margin, fees, slippage deducted."""
    if trades.empty:
        return trades

    trades = trades.copy().sort_values("time").reset_index(drop=True)

    # PnL in USD (net_edge already accounts for fees + slippage)
    trades["pnl_usd"]  = trades["net_edge"]
    trades["pnl_inr"]  = trades["pnl_usd"] * USD_TO_INR
    trades["fees_inr"] = trades["total_fees"] * USD_TO_INR
    trades["slip_inr"] = trades["total_slippage"] * USD_TO_INR

    # Cumulative
    trades["cum_pnl_inr"]   = trades["pnl_inr"].cumsum()
    trades["cum_fees_inr"]  = trades["fees_inr"].cumsum()
    trades["cum_slip_inr"]  = trades["slip_inr"].cumsum()
    trades["portfolio_inr"] = initial_margin_inr + trades["cum_pnl_inr"]

    # Drawdown
    trades["peak"]         = trades["portfolio_inr"].cummax()
    trades["drawdown_inr"] = trades["portfolio_inr"] - trades["peak"]
    trades["drawdown_pct"] = (trades["drawdown_inr"] / trades["peak"]) * 100

    # Month label
    trades["month"]       = trades["time"].dt.to_period("M")
    trades["month_label"] = trades["time"].dt.strftime("%b %Y")

    return trades


def monthly_summary(trades):
    """Month-on-month PnL summary including fees and slippage breakdown."""
    if trades.empty:
        return pd.DataFrame()

    monthly = trades.groupby("month").agg(
        start_date=("time", "first"),
        end_date=("time", "last"),
        num_trades=("pnl_inr", "count"),
        gross_pnl_inr=("gross_edge", lambda x: (x * USD_TO_INR).sum()),
        total_fees_inr=("fees_inr", "sum"),
        total_slip_inr=("slip_inr", "sum"),
        net_pnl_inr=("pnl_inr", "sum"),
        avg_pnl_inr=("pnl_inr", "mean"),
        best_trade_inr=("pnl_inr", "max"),
        worst_trade_inr=("pnl_inr", "min"),
        win_trades=("pnl_inr", lambda x: (x > 0).sum()),
        lose_trades=("pnl_inr", lambda x: (x <= 0).sum()),
    ).reset_index()

    monthly["win_rate_pct"] = (monthly["win_trades"] / monthly["num_trades"] * 100).round(1)

    # Running portfolio
    monthly["cum_pnl_inr"] = monthly["net_pnl_inr"].cumsum()
    monthly["portfolio_value_inr"] = INITIAL_MARGIN_INR + monthly["cum_pnl_inr"]
    monthly["return_pct"] = (
        monthly["net_pnl_inr"]
        / monthly["portfolio_value_inr"].shift(1).fillna(INITIAL_MARGIN_INR)
        * 100
    ).round(2)

    monthly["month_label"] = monthly["start_date"].dt.strftime("%b %Y")

    return monthly


# ===================================================================
#  CHART 1: EQUITY CURVE (Gradient Fill)
# ===================================================================
def plot_equity_curve(trades, data_source, save_path):
    fig, ax = plt.subplots(figsize=(14, 5))
    fig.patch.set_facecolor(C["bg"])
    setup_ax(ax, f"Portfolio Equity Curve | Rs.{INITIAL_MARGIN_INR:,} Initial Margin",
             xlabel="Date", ylabel="Portfolio Value (Rs.)")

    x = trades["time"]
    y = trades["portfolio_inr"]

    ax.fill_between(x, INITIAL_MARGIN_INR, y,
                    where=(y >= INITIAL_MARGIN_INR), color=C["green"], alpha=0.12)
    ax.fill_between(x, INITIAL_MARGIN_INR, y,
                    where=(y < INITIAL_MARGIN_INR), color=C["red"], alpha=0.12)
    ax.plot(x, y, color=C["cyan"], linewidth=1.8, zorder=5)

    # Initial margin line
    ax.axhline(INITIAL_MARGIN_INR, color=C["orange"], linestyle="--",
               linewidth=1, alpha=0.7, label=f"Initial: Rs.{INITIAL_MARGIN_INR:,}")

    # Peak
    peak_idx = y.idxmax()
    ax.scatter(trades.loc[peak_idx, "time"], y.max(),
               color=C["green"], s=80, zorder=10, edgecolors="white", linewidths=1.5)
    ax.annotate(f"Peak: Rs.{y.max():,.1f}",
                xy=(trades.loc[peak_idx, "time"], y.max()),
                xytext=(10, 15), textcoords="offset points",
                fontsize=9, color=C["green"], fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C["green"], lw=1))

    # Final value
    final_val = y.iloc[-1]
    total_return = ((final_val - INITIAL_MARGIN_INR) / INITIAL_MARGIN_INR) * 100
    color = C["green"] if total_return >= 0 else C["red"]
    ax.annotate(f"Final: Rs.{final_val:,.1f} ({total_return:+.1f}%)",
                xy=(x.iloc[-1], final_val),
                xytext=(-130, -25), textcoords="offset points",
                fontsize=10, color=color, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor=C["surface"],
                          edgecolor=color, alpha=0.9))

    # Cumulative fees + slippage
    ax2 = ax.twinx()
    ax2.set_facecolor("none")
    ax2.fill_between(x, 0, trades["cum_fees_inr"] + trades["cum_slip_inr"],
                     color=C["red"], alpha=0.08, label="Cumulative Costs")
    ax2.set_ylabel("Cumulative Costs (Rs.)", fontsize=9, color=C["red"])
    ax2.tick_params(colors=C["red"], labelsize=8)
    ax2.spines["right"].set_color(C["red"])

    ax.legend(loc="upper left", fontsize=9, facecolor=C["surface"],
              edgecolor=C["border"], labelcolor=C["text"])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=30)

    fig.text(0.99, 0.01, f"Data: {data_source}", fontsize=7,
             color=C["dim"], ha="right", va="bottom", alpha=0.6)

    plt.tight_layout()
    plt.savefig(save_path, dpi=180, facecolor=C["bg"], bbox_inches="tight")
    plt.close()
    print(f"   [OK] Saved: {os.path.basename(save_path)}")


# ===================================================================
#  CHART 2: MONTHLY PnL BARS + TABLE
# ===================================================================
def plot_monthly_pnl(monthly, save_path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 5.5),
                                    gridspec_kw={"width_ratios": [2, 1]})
    fig.patch.set_facecolor(C["bg"])

    # Bar chart
    setup_ax(ax1, "Month-on-Month Net PnL (Rs.)", xlabel="Month", ylabel="Net PnL (Rs.)")
    colors = [C["green"] if v >= 0 else C["red"] for v in monthly["net_pnl_inr"]]
    edge_colors = [C["green2"] if v >= 0 else C["red2"] for v in monthly["net_pnl_inr"]]

    bars = ax1.bar(monthly["month_label"], monthly["net_pnl_inr"],
                   color=colors, edgecolor=edge_colors, linewidth=1.2,
                   alpha=0.85, width=0.6, zorder=5)

    for bar, val in zip(bars, monthly["net_pnl_inr"]):
        y_pos = bar.get_height()
        va = "bottom" if val >= 0 else "top"
        offset = 3 if val >= 0 else -3
        ax1.text(bar.get_x() + bar.get_width() / 2, y_pos + offset,
                 f"Rs.{val:,.0f}", ha="center", va=va, fontsize=9,
                 fontweight="bold", color=C["text"])

    ax1.axhline(0, color=C["dim"], linewidth=0.8, alpha=0.5)
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # Summary table
    setup_ax(ax2, "Monthly Breakdown")
    ax2.axis("off")

    table_data = []
    for _, row in monthly.iterrows():
        table_data.append([
            row["month_label"],
            str(int(row["num_trades"])),
            f"Rs.{row['gross_pnl_inr']:,.0f}",
            f"-Rs.{row['total_fees_inr']:,.0f}",
            f"-Rs.{row['total_slip_inr']:,.0f}",
            f"Rs.{row['net_pnl_inr']:,.0f}",
            f"{row['win_rate_pct']:.0f}%",
            f"Rs.{row['portfolio_value_inr']:,.0f}",
        ])

    if table_data:
        table = ax2.table(
            cellText=table_data,
            colLabels=["Month", "#", "Gross", "Fees", "Slip", "Net PnL", "Win%", "Portfolio"],
            cellLoc="center",
            loc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.auto_set_column_width(col=list(range(8)))

        for key, cell in table.get_celld().items():
            cell.set_edgecolor(C["border"])
            cell.set_linewidth(0.5)
            if key[0] == 0:
                cell.set_facecolor(C["blue2"])
                cell.set_text_props(color="white", fontweight="bold")
            else:
                cell.set_facecolor(C["surface"])
                cell.set_text_props(color=C["text"])
                if key[1] == 5:  # Net PnL column
                    val = monthly.iloc[key[0] - 1]["net_pnl_inr"]
                    cell.set_text_props(
                        color=C["green"] if val >= 0 else C["red"],
                        fontweight="bold",
                    )
                if key[1] in (3, 4):  # Fees, Slip columns
                    cell.set_text_props(color=C["orange"])

        table.scale(1, 1.5)

    plt.tight_layout()
    plt.savefig(save_path, dpi=180, facecolor=C["bg"], bbox_inches="tight")
    plt.close()
    print(f"   [OK] Saved: {os.path.basename(save_path)}")


# ===================================================================
#  CHART 3: COST BREAKDOWN (Fees + Slippage)
# ===================================================================
def plot_cost_breakdown(trades, monthly, save_path):
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 10))
    fig.patch.set_facecolor(C["bg"])
    fig.suptitle("Fee & Slippage Cost Analysis", fontsize=15,
                 fontweight="bold", color=C["text"], y=0.98)

    # (1) Cumulative costs over time
    setup_ax(ax1, "Cumulative Costs Over Time (Rs.)", ylabel="Rs.")
    ax1.fill_between(trades["time"], 0, trades["cum_fees_inr"],
                     color=C["orange"], alpha=0.4, label="Fees")
    ax1.fill_between(trades["time"], trades["cum_fees_inr"],
                     trades["cum_fees_inr"] + trades["cum_slip_inr"],
                     color=C["red"], alpha=0.3, label="Slippage")
    ax1.plot(trades["time"], trades["cum_fees_inr"] + trades["cum_slip_inr"],
             color=C["red"], linewidth=1.2)
    ax1.legend(fontsize=9, facecolor=C["surface"], edgecolor=C["border"],
               labelcolor=C["text"])
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # (2) Gross vs Net edge distribution
    setup_ax(ax2, "Gross Edge vs Net Edge (after costs)", xlabel="USD")
    bins = 40
    ax2.hist(trades["gross_edge"], bins=bins, alpha=0.6, color=C["blue"],
             edgecolor=C["border"], linewidth=0.3, label="Gross Edge", zorder=5)
    ax2.hist(trades["net_edge"], bins=bins, alpha=0.6, color=C["green"],
             edgecolor=C["border"], linewidth=0.3, label="Net Edge", zorder=6)
    ax2.axvline(trades["gross_edge"].mean(), color=C["blue"], linestyle="--", lw=1.5)
    ax2.axvline(trades["net_edge"].mean(), color=C["green"], linestyle="--", lw=1.5)
    ax2.legend(fontsize=9, facecolor=C["surface"], edgecolor=C["border"],
               labelcolor=C["text"])

    # (3) Monthly cost bars
    setup_ax(ax3, "Monthly: Fees vs Slippage (Rs.)", xlabel="Month", ylabel="Rs.")
    if not monthly.empty:
        x_pos = range(len(monthly))
        w = 0.35
        ax3.bar([p - w / 2 for p in x_pos], monthly["total_fees_inr"],
                width=w, color=C["orange"], alpha=0.85, label="Fees", zorder=5)
        ax3.bar([p + w / 2 for p in x_pos], monthly["total_slip_inr"],
                width=w, color=C["red"], alpha=0.85, label="Slippage", zorder=5)
        ax3.set_xticks(list(x_pos))
        ax3.set_xticklabels(monthly["month_label"], fontsize=8)
        ax3.legend(fontsize=9, facecolor=C["surface"], edgecolor=C["border"],
                   labelcolor=C["text"])

    # (4) Cost as % of gross edge (pie)
    setup_ax(ax4, "Cost Structure (% of Gross Edge)")
    total_gross = trades["gross_edge"].sum()
    total_fees  = trades["total_fees"].sum()
    total_slip  = trades["total_slippage"].sum()
    total_net   = trades["net_edge"].sum()
    if total_gross > 0:
        sizes  = [total_fees, total_slip, total_net]
        labels = [
            f"Fees: {total_fees / total_gross * 100:.1f}%",
            f"Slippage: {total_slip / total_gross * 100:.1f}%",
            f"Net Profit: {total_net / total_gross * 100:.1f}%",
        ]
        pie_colors = [C["orange"], C["red"], C["green"]]
        wedges, texts, autotexts = ax4.pie(
            sizes, labels=labels, autopct="%1.1f%%",
            colors=pie_colors, explode=[0.03, 0.03, 0.05],
            textprops={"color": C["text"], "fontsize": 9},
            wedgeprops={"edgecolor": C["border"], "linewidth": 1},
        )
        for at in autotexts:
            at.set_fontweight("bold")
            at.set_fontsize(8)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(save_path, dpi=180, facecolor=C["bg"], bbox_inches="tight")
    plt.close()
    print(f"   [OK] Saved: {os.path.basename(save_path)}")


# ===================================================================
#  CHART 4: DRAWDOWN
# ===================================================================
def plot_drawdown(trades, save_path):
    fig, ax = plt.subplots(figsize=(14, 4))
    fig.patch.set_facecolor(C["bg"])
    setup_ax(ax, "Portfolio Drawdown", xlabel="Date", ylabel="Drawdown (%)")

    x = trades["time"]
    y = trades["drawdown_pct"]

    ax.fill_between(x, 0, y, color=C["red"], alpha=0.25, zorder=3)
    ax.plot(x, y, color=C["red"], linewidth=1.2, alpha=0.9, zorder=5)
    ax.axhline(0, color=C["dim"], linewidth=0.5, alpha=0.5)

    min_dd_idx = y.idxmin()
    max_dd = y.min()
    ax.scatter(trades.loc[min_dd_idx, "time"], max_dd,
               color=C["red"], s=70, zorder=10, edgecolors="white", linewidths=1.5)
    ax.annotate(f"Max DD: {max_dd:.1f}%",
                xy=(trades.loc[min_dd_idx, "time"], max_dd),
                xytext=(15, -15), textcoords="offset points",
                fontsize=9, color=C["red"], fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=C["red"], lw=1))

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.xticks(rotation=30)

    plt.tight_layout()
    plt.savefig(save_path, dpi=180, facecolor=C["bg"], bbox_inches="tight")
    plt.close()
    print(f"   [OK] Saved: {os.path.basename(save_path)}")


# ===================================================================
#  CHART 5: SIGNAL ANALYSIS
# ===================================================================
def plot_signal_analysis(trades, save_path):
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 10))
    fig.patch.set_facecolor(C["bg"])
    fig.suptitle("Signal & Edge Analysis", fontsize=15, fontweight="bold",
                 color=C["text"], y=0.98)

    # (1) Edge distribution
    setup_ax(ax1, "Net Edge Distribution (Rs.)", ylabel="Frequency")
    edges = trades["pnl_inr"]
    bins = np.linspace(edges.min(), edges.max(), 50)
    _, _, patches = ax1.hist(edges, bins=bins, edgecolor=C["border"], linewidth=0.5, zorder=5)
    for p, b in zip(patches, bins):
        p.set_facecolor(C["green"] if b >= 0 else C["red"])
        p.set_alpha(0.75)
    ax1.axvline(edges.mean(), color=C["yellow"], linestyle="--", linewidth=1.5,
                label=f"Mean: Rs.{edges.mean():.2f}")
    ax1.axvline(edges.median(), color=C["purple"], linestyle="--", linewidth=1.5,
                label=f"Median: Rs.{edges.median():.2f}")
    ax1.legend(fontsize=8, facecolor=C["surface"], edgecolor=C["border"], labelcolor=C["text"])

    # (2) Strategy split
    setup_ax(ax2, "Strategy Split")
    if "strategy" in trades.columns:
        counts = trades["strategy"].value_counts()
        explode = [0.04] * len(counts)
        wedge_colors = [C["blue"], C["purple"], C["orange"], C["pink"]][:len(counts)]
        wedges, texts, autotexts = ax2.pie(
            counts.values, labels=counts.index, autopct="%1.1f%%",
            explode=explode, colors=wedge_colors,
            textprops={"color": C["text"], "fontsize": 10},
            wedgeprops={"edgecolor": C["border"], "linewidth": 1},
        )
        for at in autotexts:
            at.set_fontweight("bold")

    # (3) Hourly frequency
    setup_ax(ax3, "Signal Frequency by Hour (UTC)", xlabel="Hour", ylabel="Count")
    hours = trades["time"].dt.hour
    hour_counts = hours.value_counts().sort_index()
    all_hours = pd.Series(0, index=range(24))
    all_hours.update(hour_counts)
    bar_colors = [plt.cm.cool(h / 24) for h in range(24)]
    ax3.bar(all_hours.index, all_hours.values, color=bar_colors,
            edgecolor=C["border"], linewidth=0.5, alpha=0.85, zorder=5)
    ax3.set_xticks(range(0, 24, 2))

    # (4) Daily PnL scatter
    setup_ax(ax4, "Daily Net PnL (Rs.)", xlabel="Date", ylabel="Rs.")
    daily = trades.set_index("time").resample("D")["pnl_inr"].sum().reset_index()
    scatter_colors = [C["green"] if v >= 0 else C["red"] for v in daily["pnl_inr"]]
    ax4.scatter(daily["time"], daily["pnl_inr"], c=scatter_colors,
                s=30, alpha=0.7, edgecolors="white", linewidths=0.3, zorder=5)
    ax4.axhline(0, color=C["dim"], linewidth=0.8, alpha=0.5)
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    plt.setp(ax4.xaxis.get_majorticklabels(), rotation=30, ha="right")

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.savefig(save_path, dpi=180, facecolor=C["bg"], bbox_inches="tight")
    plt.close()
    print(f"   [OK] Saved: {os.path.basename(save_path)}")


# ===================================================================
#  CHART 6: FULL DASHBOARD
# ===================================================================
def plot_dashboard(trades, monthly, data_source, save_path):
    fig = plt.figure(figsize=(20, 14))
    fig.patch.set_facecolor(C["bg"])

    gs = fig.add_gridspec(3, 3, hspace=0.35, wspace=0.3,
                          left=0.06, right=0.97, top=0.93, bottom=0.05)

    fig.suptitle(
        "BTC Options Arbitrage - Full Backtest Dashboard",
        fontsize=18, fontweight="bold", color=C["text"], y=0.97,
    )
    fig.text(0.5, 0.945,
             f"Margin: Rs.{INITIAL_MARGIN_INR:,} | Strike: {STRIKE:,} | "
             f"Fees: {TAKER_FEE_OPTION_BPS}bps opt / {TAKER_FEE_FUTURE_BPS}bps fut | "
             f"Slippage: ${SLIPPAGE_USD_OPTION}/opt + ${SLIPPAGE_USD_FUTURE}/fut | {data_source}",
             fontsize=9, color=C["dim"], ha="center")

    # (1) Equity Curve
    ax1 = fig.add_subplot(gs[0, :2])
    setup_ax(ax1, "Equity Curve (Rs.)", ylabel="Rs.")
    x, y = trades["time"], trades["portfolio_inr"]
    ax1.fill_between(x, INITIAL_MARGIN_INR, y,
                     where=(y >= INITIAL_MARGIN_INR), color=C["green"], alpha=0.1)
    ax1.fill_between(x, INITIAL_MARGIN_INR, y,
                     where=(y < INITIAL_MARGIN_INR), color=C["red"], alpha=0.1)
    ax1.plot(x, y, color=C["cyan"], linewidth=1.5)
    ax1.axhline(INITIAL_MARGIN_INR, color=C["orange"], linestyle="--", linewidth=0.8, alpha=0.6)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # (2) KPI Cards
    ax_kpi = fig.add_subplot(gs[0, 2])
    ax_kpi.set_facecolor(C["card"])
    ax_kpi.axis("off")

    final_val  = trades["portfolio_inr"].iloc[-1]
    total_ret  = ((final_val - INITIAL_MARGIN_INR) / INITIAL_MARGIN_INR) * 100
    max_dd     = trades["drawdown_pct"].min()
    sharpe     = (trades["pnl_inr"].mean() / trades["pnl_inr"].std()) * np.sqrt(252 * 12) if trades["pnl_inr"].std() > 0 else 0
    win_rate   = (trades["pnl_inr"] > 0).mean() * 100
    total_fees = trades["fees_inr"].sum()
    total_slip = trades["slip_inr"].sum()

    kpis = [
        ("Final Value",      f"Rs.{final_val:,.0f}",    C["cyan"]),
        ("Total Return",     f"{total_ret:+.1f}%",       C["green"] if total_ret >= 0 else C["red"]),
        ("Total Trades",     f"{len(trades):,}",         C["blue"]),
        ("Win Rate",         f"{win_rate:.1f}%",          C["green"] if win_rate >= 50 else C["orange"]),
        ("Sharpe Ratio",     f"{sharpe:.2f}",             C["purple"]),
        ("Max Drawdown",     f"{max_dd:.1f}%",            C["red"]),
        ("Total Fees Paid",  f"Rs.{total_fees:,.1f}",    C["orange"]),
        ("Total Slippage",   f"Rs.{total_slip:,.1f}",    C["red"]),
    ]
    for i, (label, value, color) in enumerate(kpis):
        y_pos = 0.92 - i * 0.115
        ax_kpi.text(0.05, y_pos, label, fontsize=9, color=C["dim"],
                    transform=ax_kpi.transAxes, va="top")
        ax_kpi.text(0.95, y_pos, value, fontsize=11, color=color, fontweight="bold",
                    transform=ax_kpi.transAxes, va="top", ha="right")

    # (3) Monthly PnL
    ax2 = fig.add_subplot(gs[1, :2])
    setup_ax(ax2, "Monthly Net PnL (Rs.)", ylabel="Rs.")
    bar_colors = [C["green"] if v >= 0 else C["red"] for v in monthly["net_pnl_inr"]]
    bars = ax2.bar(monthly["month_label"], monthly["net_pnl_inr"],
                   color=bar_colors,
                   edgecolor=[C["green2"] if v >= 0 else C["red2"] for v in monthly["net_pnl_inr"]],
                   linewidth=1, alpha=0.85, zorder=5)
    for bar, val in zip(bars, monthly["net_pnl_inr"]):
        y_pos = bar.get_height()
        va = "bottom" if val >= 0 else "top"
        ax2.text(bar.get_x() + bar.get_width() / 2, y_pos + (2 if val >= 0 else -2),
                 f"Rs.{val:,.0f}", ha="center", va=va, fontsize=8,
                 fontweight="bold", color=C["text"])
    ax2.axhline(0, color=C["dim"], linewidth=0.5)

    # (4) Drawdown
    ax3 = fig.add_subplot(gs[1, 2])
    setup_ax(ax3, "Drawdown (%)", ylabel="%")
    ax3.fill_between(trades["time"], 0, trades["drawdown_pct"],
                     color=C["red"], alpha=0.3, zorder=3)
    ax3.plot(trades["time"], trades["drawdown_pct"], color=C["red"], linewidth=1, zorder=5)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # (5) Edge Distribution
    ax4 = fig.add_subplot(gs[2, 0])
    setup_ax(ax4, "Edge Distribution (Rs.)", xlabel="Edge")
    edges = trades["pnl_inr"]
    bins = np.linspace(edges.min(), edges.max(), 40)
    _, _, patches = ax4.hist(edges, bins=bins, edgecolor=C["border"], linewidth=0.3, zorder=5)
    for p, b in zip(patches, bins):
        p.set_facecolor(C["green"] if b >= 0 else C["red"])
        p.set_alpha(0.7)

    # (6) Cumulative costs vs profits
    ax5 = fig.add_subplot(gs[2, 1])
    setup_ax(ax5, "Profit vs Costs (Rs.)", ylabel="Rs.")
    ax5.plot(trades["time"], trades["cum_pnl_inr"], color=C["green"],
             linewidth=1.5, label="Net PnL", zorder=5)
    ax5.plot(trades["time"], trades["cum_fees_inr"], color=C["orange"],
             linewidth=1.2, label="Fees", zorder=5)
    ax5.plot(trades["time"], trades["cum_slip_inr"], color=C["red"],
             linewidth=1.2, label="Slippage", zorder=5)
    ax5.legend(fontsize=8, facecolor=C["surface"], edgecolor=C["border"],
               labelcolor=C["text"])
    ax5.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    # (7) Win/Loss by Month
    ax6 = fig.add_subplot(gs[2, 2])
    setup_ax(ax6, "Win vs Loss by Month")
    if not monthly.empty:
        x_pos = range(len(monthly))
        w = 0.35
        ax6.bar([p - w / 2 for p in x_pos], monthly["win_trades"],
                width=w, color=C["green"], alpha=0.8, label="Wins", zorder=5)
        ax6.bar([p + w / 2 for p in x_pos], monthly["lose_trades"],
                width=w, color=C["red"], alpha=0.8, label="Losses", zorder=5)
        ax6.set_xticks(list(x_pos))
        ax6.set_xticklabels(monthly["month_label"], fontsize=8)
        ax6.legend(fontsize=8, facecolor=C["surface"], edgecolor=C["border"],
                   labelcolor=C["text"])

    plt.savefig(save_path, dpi=150, facecolor=C["bg"], bbox_inches="tight")
    plt.close()
    print(f"   [OK] Saved: {os.path.basename(save_path)}")


# ===================================================================
#  MAIN EXECUTION
# ===================================================================
def main():
    print("=" * 65)
    print("  BTC OPTIONS ARBITRAGE BACKTESTER")
    print("  Initial Margin: Rs.1,000  |  Strike: 65,000")
    print("  Fees: 3bps/opt + 5bps/fut  |  Slippage: 5bps/opt + 2bps/fut")
    print("=" * 65 + "\n")

    # -- Step 1: Fetch data --
    call_df, put_df, future_df, strike, data_source = fetch_delta_data()

    if call_df is None or (hasattr(call_df, '__len__') and len(call_df) == 0):
        call_df, put_df, future_df, strike = generate_simulated_data(months=3)
        data_source = "SIMULATED (3 months, realistic pricing)"

    print(f"[i] Data source: {data_source}")
    print(f"[i] Bars loaded: {len(call_df):,}")
    print(f"[i] Initial Margin: Rs.{INITIAL_MARGIN_INR:,} (~ ${INITIAL_MARGIN_USD:.2f})\n")

    # -- Step 2: Run backtest with costs --
    print("[*] Running Conversion/Reversal backtest (with fees + slippage)...")
    res_df, trades_raw, summary = backtest_with_costs(
        call_df, put_df, future_df, strike, notional=1
    )

    if trades_raw.empty:
        print("[X] No tradable signals after fees + slippage. Edge too small.")
        return

    # -- Step 3: Portfolio metrics --
    trades = compute_portfolio(trades_raw)
    monthly = monthly_summary(trades)

    # -- Step 4: Print summary --
    print("\n" + "=" * 65)
    print("  BACKTEST SUMMARY (after fees + slippage)")
    print("=" * 65)
    final_val   = trades["portfolio_inr"].iloc[-1]
    total_ret   = ((final_val - INITIAL_MARGIN_INR) / INITIAL_MARGIN_INR) * 100
    win_rate    = (trades["pnl_inr"] > 0).mean() * 100
    total_fees  = trades["fees_inr"].sum()
    total_slip  = trades["slip_inr"].sum()
    total_costs = total_fees + total_slip

    stats = {
        "Total Bars Scanned":     f"{summary['total_bars_scanned']:,}",
        "Signals Found":          f"{summary['signals_found']:,}",
        "Signal Rate":            f"{summary['signal_rate_pct']:.2f}%",
        "Contract Size":          f"{summary['contract_size_btc']} BTC",
        "---":                    "---",
        "Avg Gross Edge (USD)":   f"${summary['avg_gross_edge_usd']:.6f}",
        "Avg Fees/Trade (USD)":   f"${summary['avg_total_fees_usd']:.6f}",
        "Avg Slippage/Trade":     f"${summary['avg_slippage_usd']:.6f}",
        "Avg Net Edge (USD)":     f"${summary['avg_net_edge_usd']:.6f}",
        "----":                   "----",
        "Total Fees Paid":        f"Rs.{total_fees:,.1f}",
        "Total Slippage Cost":    f"Rs.{total_slip:,.1f}",
        "Total Costs":            f"Rs.{total_costs:,.1f}",
        "-----":                  "-----",
        "Initial Margin":         f"Rs.{INITIAL_MARGIN_INR:,.0f}",
        "FINAL PORTFOLIO":        f"Rs.{final_val:,.1f}",
        "TOTAL RETURN":           f"{total_ret:+.1f}%",
        "Win Rate":               f"{win_rate:.1f}%",
        "Max Drawdown":           f"{trades['drawdown_pct'].min():.1f}%",
    }
    for k, v in stats.items():
        if k.startswith("-"):
            print("  " + "-" * 50)
        else:
            print(f"  {k:.<35s} {v:>15s}")

    # -- Step 5: Month-on-Month PnL --
    print("\n" + "=" * 65)
    print("  MONTH-ON-MONTH PnL (Net of Fees + Slippage)")
    print("=" * 65)
    header = (f"  {'Month':<10s} {'Trades':>7s} {'Gross':>10s} {'Fees':>10s} "
              f"{'Slip':>10s} {'Net PnL':>10s} {'Return':>8s} {'Portfolio':>12s}")
    print(header)
    print("  " + "-" * (len(header) - 2))

    for _, row in monthly.iterrows():
        marker = "+" if row["net_pnl_inr"] >= 0 else "-"
        print(f"  {marker} {row['month_label']:<8s} {row['num_trades']:>5.0f}   "
              f"Rs.{row['gross_pnl_inr']:>7,.0f}  "
              f"-Rs.{row['total_fees_inr']:>6,.0f}  "
              f"-Rs.{row['total_slip_inr']:>6,.0f}  "
              f"Rs.{row['net_pnl_inr']:>7,.0f}  "
              f"{row['return_pct']:>+6.1f}%  "
              f"Rs.{row['portfolio_value_inr']:>9,.0f}")

    # -- Final P&L Summary Box --
    print("\n" + "=" * 65)
    print("  FINAL P&L STATEMENT")
    print("=" * 65)
    gross_total = trades["gross_edge"].sum() * USD_TO_INR
    print(f"  Starting Capital ............... Rs.{INITIAL_MARGIN_INR:>10,.0f}")
    print(f"  Gross Profit ................... Rs.{gross_total:>10,.1f}")
    print(f"  (-) Exchange Fees .............. Rs.{total_fees:>10,.1f}")
    print(f"  (-) Slippage ................... Rs.{total_slip:>10,.1f}")
    print(f"  (-) Total Costs ................ Rs.{total_costs:>10,.1f}")
    print(f"  " + "-" * 42)
    net_profit = final_val - INITIAL_MARGIN_INR
    print(f"  = NET PROFIT ................... Rs.{net_profit:>10,.1f}")
    print(f"  = FINAL PORTFOLIO VALUE ........ Rs.{final_val:>10,.1f}")
    print(f"  = TOTAL RETURN ................. {total_ret:>+10.1f}%")
    print("=" * 65)

    # -- Step 6: Generate charts --
    print("\n[*] Generating colorful charts...")

    chart_dir = os.path.join(OUTPUT_DIR, "backtest_charts")
    os.makedirs(chart_dir, exist_ok=True)

    plot_equity_curve(trades, data_source,
                      os.path.join(chart_dir, "01_equity_curve.png"))
    plot_monthly_pnl(monthly,
                     os.path.join(chart_dir, "02_monthly_pnl.png"))
    plot_cost_breakdown(trades, monthly,
                        os.path.join(chart_dir, "03_cost_breakdown.png"))
    plot_drawdown(trades,
                  os.path.join(chart_dir, "04_drawdown.png"))
    plot_signal_analysis(trades,
                         os.path.join(chart_dir, "05_signal_analysis.png"))
    plot_dashboard(trades, monthly, data_source,
                   os.path.join(chart_dir, "06_full_dashboard.png"))

    # -- Step 7: Save CSVs --
    csv_path = os.path.join(OUTPUT_DIR, "backtest_trades.csv")
    trades.to_csv(csv_path, index=False)
    print(f"   [OK] Saved: backtest_trades.csv")

    monthly_csv = os.path.join(OUTPUT_DIR, "monthly_pnl.csv")
    monthly.to_csv(monthly_csv, index=False)
    print(f"   [OK] Saved: monthly_pnl.csv")

    # -- Step 8: Box spread demo --
    print("\n" + "=" * 65)
    print("  BOX SPREAD SNAPSHOT (with costs)")
    print("=" * 65)
    k1, k2 = 64000, 66000
    call_k1, call_k2 = 1800, 700
    put_k1, put_k2 = 650, 1750
    box = detect_box_spread(call_k1, call_k2, put_k1, put_k2, k1, k2, notional=1)
    for k, v in box.items():
        if isinstance(v, float):
            print(f"  {k:.<30s} {v:>12,.4f}")
        else:
            print(f"  {k:.<30s} {str(v):>12s}")

    print("\n" + "=" * 65)
    print(f"  DONE! All charts saved to: {chart_dir}")
    print(f"  Open the 'backtest_charts' folder to view visualizations")
    print("=" * 65)


if __name__ == "__main__":
    main()