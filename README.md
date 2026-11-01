# Options IV Arbitrage Algorithm (TypeScript & Node.js)

An automated crypto options scanning and arbitrage algorithm with **Telegram Bot integration**, implemented in **TypeScript and Node.js**, designed for **Implied Volatility (IV) Mispricing Arbitrage** between **Delta Exchange** and **CoinSwitch PRO**.

---

## 📌 Strategy Explained

### 1. The Core Mispricing Anomaly
| Metric | Exchange 1: Delta Exchange | Exchange 2: CoinSwitch PRO |
| :--- | :--- | :--- |
| **Daily Expiry Time** | **5:30 PM IST** (12:00 UTC) | **1:30 PM IST** (08:00 UTC) |
| **Duration Difference** | **+4 extra hours** to expiry | **-4 fewer hours** to expiry |
| **Theoretical Value** | Must be **HIGHER** (more $\theta$ time value) | Must be **LOWER** (less $\theta$ time value) |

Under standard Black-Scholes / option pricing theory, an option that expires **4 hours later** has more time value ($\theta$) and must trade at a higher price than an option that expires earlier for the identical underlying asset and strike.

### 2. The Arbitrage Rule: `CoinSwitch Value ≥ Delta Exchange Value`
Whenever:
$$\text{CoinSwitch Option Price} \ge \text{Delta Exchange Option Price}$$
CoinSwitch’s Implied Volatility (IV) is significantly inflated and overpriced.
* **The Play:** 
  - **SELL** the overpriced CoinSwitch option (higher premium, decays faster, settles at 1:30 PM IST).
  - *(Optional / Box Hedge)* **BUY** the cheaper Delta option (lower premium, extra 4 hours of protection until 5:30 PM IST).
  - Collect the net credit spread with positive expected value (EV)!

### 3. The Daily 1:30 PM IST Trade Cycle
* **1:30 PM IST Daily:** 
  1. Yesterday’s CoinSwitch trade expires and automatically settles. Close or verify settlement.
  2. The algorithm automatically triggers an alert on Telegram with the top next-day mispriced strikes.
  3. Re-enter the new position for next day’s 1:30 PM expiry.

---

## ⚖️ Fund Imbalance & Transfer Alerts

When trading across two separate exchanges, profits accumulate unevenly, or margin requirements shift:
* The algorithm continuously tracks wallet balances across **both Delta Exchange and CoinSwitch**.
* If one account holds significantly more funds than the other (e.g. difference $\ge 25\%$):
  - **Urgent Telegram Alert** is triggered automatically:
  ```text
  ⚠️ FUND IMBALANCE ALERT ⚠️
  Delta Exchange : $1,600 (₹1,38,400) [80%]
  CoinSwitch     : $400   (₹34,600)   [20%]
  
  👉 ACTION REQUIRED:
  Transfer ~$600 (₹51,900) from Delta Exchange to CoinSwitch to equalize margin for arbitrage!
  ```

---

## 🤖 Telegram Bot Commands

Send any of these messages to your Telegram bot:

| Command | Action |
| :--- | :--- |
| `options` or `/options` | Scans Delta & CoinSwitch options and returns **ATM**, **OTM Calls**, **OTM Puts**, and highlights **CoinSwitch $\ge$ Delta** arbitrage signals. |
| `arb` or `/arb` | Filters and displays **ONLY** active arbitrage signals where CoinSwitch $\ge$ Delta. |
| `balance` or `/balance` | Checks wallet equity on both exchanges and shows fund balance status & transfer suggestions. |
| `trade` or `/trade` | Displays the 1:30 PM IST rollover execution checklist and countdown timer. |
| `status` or `/status` | Shows live BTC spot price, exchange connectivity status, and bot health. |
| `help` or `/help` | Displays available commands and strategy rules. |

---

## 🚀 Quick Setup Guide (TypeScript / Node.js)

### Step 1: Install Dependencies
```bash
npm install
```

### Step 2: Configure `.env`
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
1. **Telegram:**
   - Message `@BotFather` on Telegram to create a bot and copy your `TELEGRAM_BOT_TOKEN`.
   - Message `@userinfobot` to get your numeric `TELEGRAM_CHAT_ID`.
2. **Delta Exchange:**
   - Add `DELTA_API_KEY` and `DELTA_API_SECRET`.
3. **CoinSwitch PRO:**
   - Add `COINSWITCH_API_KEY` and `COINSWITCH_SECRET_KEY` (Ed25519 hex).

### Step 3: Test Integration (NPM Scripts)
```bash
# 1. Test options scanner & arbitrage detection
npm run scan

# 2. Test wallet balance check & imbalance monitor
npm run balance

# 3. Test Telegram push notification
npm run test-telegram
```

### Step 4: Run the Algorithm Service
```bash
# Development (with live tsx execution)
npm start

# Or compile to JavaScript and run in production
npm run build
npm run prod
```

The algorithm will:
1. Listen 24/7 on Telegram for user commands like `options` and `balance`.
2. Automatically check account balances every 30 minutes and alert you if funds become imbalanced.
3. Automatically fire daily rollover alerts at **1:30 PM IST** to close yesterday's trade and enter the next day's trade.
