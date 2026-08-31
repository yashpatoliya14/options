import os
import sys
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
        print("Warning: DELTA_API_KEY or DELTA_API_SECRET not found in environment.")
        print("The script will run but trading actions will likely fail authentication.")

    # Load live configuration
    config_path = Path(__file__).parent / "live" / "config_live.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    # Convert yaml config to StrategyParams
    params = StrategyParams().overlay(config_data)

    # Initialize Delta Client
    env_base_url = os.getenv("DELTA_BASE_URL", "")
    base_url = env_base_url if env_base_url else config_data.get("delta_base_url", "https://cdn-ind.testnet.deltaex.org")
    client = DeltaRestClient(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret
    )

    # Initialize Engine Components
    clock = WallClock()
    provider = LiveDataProvider(client, clock)
    executor = LiveExecutor(client, provider, params)
    engine = StrategyEngine(params)

    # Initialize and start the live runner
    state_path = config_data.get("state_path", "state/live_position.json")
    trade_log_path = config_data.get("trade_log_path", "state/trades.jsonl")
    poll_seconds = config_data.get("poll_seconds", 30)

    import requests

    def send_telegram_message(message: str) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
        
        if not enabled or not token or not chat_id:
            return
            
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
        except Exception as e:
            print(f"Failed to send telegram message: {e}")

    runner = LiveRunner(
        engine=engine,
        provider=provider,
        executor=executor,
        params=params,
        state_path=state_path,
        trade_log_path=trade_log_path,
        poll_seconds=poll_seconds,
        notify_fn=send_telegram_message
    )

    print(f"Starting LiveRunner...")
    print(f"Strategy: EMA {params.ema_fast}/{params.ema_slow} | Trend ADX > {params.adx_min}")
    print(f"Execution: Hold to Expiry (TP: {params.tp_pct*100}%, SL: {params.sl_pct*100}%)")
    print(f"Polling Delta Exchange every {poll_seconds} seconds.")
    
    send_telegram_message(
        f"🚀 **Live Trading Algorithm Started!**\n\n"
        f"📊 **Strategy:** EMA {params.ema_fast}/{params.ema_slow} (ADX > {params.adx_min})\n"
        f"🎯 **Execution:** Hold to Expiry\n"
        f"⏳ **Polling:** {poll_seconds}s"
    )
    
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        print("\nLive trading stopped by user.")
        send_telegram_message("🛑 **Live Trading Algorithm Stopped manually.**")

if __name__ == "__main__":
    run_live()
