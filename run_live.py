"""
Supertrend 1H Options — Live Trading Runner
=============================================
SUPERTREND: 1H | ATR PERIOD: 15 | MULTIPLIER: 1.5

Connects to Delta Exchange India, runs the Supertrend directional
options strategy with Bull Put / Bear Put spreads.

Usage:
    python run_live.py

Requires:
    - .env file with Delta Exchange API credentials
    - live/config_live.yaml for strategy parameters
"""

import os
import sys
import signal
import yaml
from pathlib import Path
from dotenv import load_dotenv

from engine import StrategyEngine, StrategyParams
from live.delta_rest import DeltaRestClient
from live.live_data_provider import LiveDataProvider, WallClock
from live.live_executor import LiveExecutor
from live.live_runner import LiveRunner


def run_live():
    # Load environment variables from .env file
    load_dotenv()

    api_key = os.getenv("DELTA_API_KEY", "")
    api_secret = os.getenv("DELTA_API_SECRET", "")

    if not api_key or not api_secret:
        print("[ERROR] DELTA_API_KEY or DELTA_API_SECRET not found in .env")
        print("Please configure your .env file with Delta Exchange credentials.")
        sys.exit(1)

    # Load live configuration
    config_path = Path(__file__).parent / "live" / "config_live.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    # Environment variable overrides
    env_qty = os.getenv("TRADE_QTY")
    if env_qty:
        try:
            config_data["qty"] = int(env_qty)
        except ValueError:
            print(f"[WARN] Invalid TRADE_QTY in environment: {env_qty}")

    env_underlying = os.getenv("UNDERLYING_SYMBOL")
    if env_underlying:
        config_data["candle_symbol"] = env_underlying

    env_asset = os.getenv("UNDERLYING_ASSET")
    if env_asset:
        config_data["underlying"] = env_asset

    # Convert yaml config to StrategyParams
    params = StrategyParams().overlay(config_data)

    # Initialize Delta Client
    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
    if use_testnet:
        base_url = os.getenv("DELTA_TESTNET_URL", "https://cdn-ind.testnet.deltaex.org")
    else:
        base_url = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")

    client = DeltaRestClient(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
    )

    # Initialize Engine Components
    clock = WallClock()
    provider = LiveDataProvider(client, clock)
    executor = LiveExecutor(client, provider, params)
    engine = StrategyEngine(params)

    # Initialize and start the live runner
    state_path = config_data.get("state_path", "state/live_position.json")
    trade_log_path = config_data.get("trade_log_path", "state/trades.jsonl")
    poll_seconds = config_data.get("poll_seconds", 10)

    import requests

    def send_telegram_message(message: str) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"

        if not enabled or not token or not chat_id:
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            print(f"[WARN] Telegram send failed: {e}")

    runner = LiveRunner(
        engine=engine,
        provider=provider,
        executor=executor,
        params=params,
        state_path=state_path,
        trade_log_path=trade_log_path,
        poll_seconds=poll_seconds,
        notify_fn=send_telegram_message,
    )

    # Print startup info
    mode = "TESTNET" if use_testnet else "LIVE"
    print(f"\n{'=' * 60}")
    print(f"  SUPERTREND OPTIONS ALGO - {mode}")
    print(f"  SUPERTREND: 1H | ATR PERIOD: {params.supertrend_atr_period} | MULTIPLIER: {params.supertrend_multiplier}")
    print(f"{'=' * 60}")
    print(f"  Exchange:       Delta Exchange India ({mode})")
    print(f"  Base URL:       {base_url}")
    print(f"  Underlying:     {params.underlying} ({params.candle_symbol})")
    print(f"  Resolution:     {params.resolution}")
    print(f"  Trend Filter:   EMA {params.trend_filter_period} ({'ON' if params.trend_filter_enabled else 'OFF'})")
    print(f"  Min Trend Bars: {params.min_trend_bars}")
    print(f"  Signal Cut:     {'ON' if params.exit_on_opposite_signal else 'OFF'}")
    print(f"  Cooldown:       {params.cooldown_seconds // 3600}h")
    print(f"  TP / SL:        {params.take_profit_pct*100:.0f}% / {params.stop_loss_pct*100:.0f}%")
    print(f"  Qty:            {params.qty}")
    print(f"  Polling:        {poll_seconds}s")
    print(f"  Telegram:       {'ON' if os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true' else 'OFF'}")
    print(f"{'=' * 60}")
    print(f"  Starting... Press Ctrl+C to stop.\n")

    send_telegram_message(
        f"🚀 <b>Live Trading Started!</b>\n\n"
        f"📊 <b>Strategy:</b> Supertrend 1H (ATR {params.supertrend_atr_period}, Mult {params.supertrend_multiplier})\n"
        f"📈 <b>Trend Filter:</b> EMA {params.trend_filter_period} ({'ON' if params.trend_filter_enabled else 'OFF'})\n"
        f"🎯 <b>Exit:</b> TP {params.take_profit_pct*100:.0f}% / SL {params.stop_loss_pct*100:.0f}%\n"
        f"⏳ <b>Cooldown:</b> {params.cooldown_seconds // 3600}h\n"
        f"📦 <b>Qty:</b> {params.qty}\n"
        f"🌐 <b>Mode:</b> {mode}"
    )

    # Graceful shutdown
    def shutdown_handler(signum, frame):
        print("\n[INFO] Shutting down gracefully...")
        send_telegram_message("🛑 <b>Live Trading Stopped.</b>")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Live trading stopped by user.")
        send_telegram_message("🛑 <b>Live Trading Stopped manually.</b>")


if __name__ == "__main__":
    run_live()
