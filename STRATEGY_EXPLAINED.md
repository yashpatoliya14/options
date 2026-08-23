# The "AlgoTest Classic" Crypto Strategy
**Deployed via Azure Virtual Machine**

## Overview
This strategy relies on a principle highly popularized by platforms like AlgoTest and Cryptomaty: **Non-directional premium selling with strict risk management**. 

Instead of trying to predict if the market will go up or down, we profit from the passage of time (Theta decay) and falling volatility. The strategy is mathematically designed to absorb the massive bid-ask spreads (slippage) found in crypto options markets by trading infrequently and relying on hard Stop-Losses.

## The Strategy Rules (Short Strangle)

### 1. Entry Mechanics (Weekly)
Every 7 days, the algorithm fetches the current underlying price of Bitcoin.
It calculates strikes that are **10% Out-Of-The-Money (OTM)** in both directions.
- **Example:** If BTC is at $65,000, it calculates the $71,500 Call and the $58,500 Put.
- It places **Sell** orders for both of these options, collecting a cash premium upfront.

### 2. Risk Management (The 30% Stop Loss)
The danger of selling options is unlimited risk if the market explodes. To prevent this, the bot checks the price of these options **every 60 seconds**.
- If the premium of either option spikes to **30% above the entry price**, the bot instantly fires a market Buy order to close that leg.
- **Why 30%?** In backtesting over 1 year with realistic 8% dynamic slippage penalties, a 30% stop-loss prevents catastrophic drawdowns during massive BTC bull runs, while giving enough breathing room to avoid getting "whipsawed" out of the trade by normal intraday noise.

### 3. Exit Mechanics
If the stop loss is not hit, the options are held all the way to expiry (7 days). Because they were 10% OTM, they usually expire worthless, allowing you to keep 100% of the premium collected.

---

## Azure Virtual Machine Deployment

To run this strategy live on your Azure VM, use the provided `live_algo.py` script. It has been engineered as a 24/7 daemon process with full logging capabilities.

### Prerequisites
1. SSH into your Azure VM.
2. Install dependencies:
   ```bash
   pip install requests python-dotenv
   ```
3. Create a `.env` file in the same directory as the script and add your Delta Exchange API keys:
   ```text
   DELTA_API_KEY=your_api_key_here
   DELTA_API_SECRET=your_api_secret_here
   ```

### Running the Bot
To run the bot in the background so it doesn't stop when you close your SSH terminal, use `nohup`:

```bash
nohup python live_algo.py > live_algo.log 2>&1 &
```

### Monitoring the Bot
The bot uses Python's standard `logging` library. You can watch the bot's live decisions, price checks, and stop-loss triggers by tailing the log file:

```bash
tail -f live_algo.log
```
