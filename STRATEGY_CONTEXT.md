# Delta Exchange India: Gold Trend + Grid Hedging Strategy Context

> **IMPORTANT FOR AI AGENTS**: Whenever the user asks to fix, modify, debug, or extend this bot, read this file first. It contains the complete source of truth for strategy logic, instruments, lot allocations, risk limits, and code architecture.

---

## 1. Executive Summary

This codebase is a production-grade algorithmic trading bot engineered strictly for **Delta Exchange India** (`https://api.india.delta.exchange`).

It implements a quantitative **4-Hour Supertrend Trend-Following + Adverse Grid Averaging + Dynamic Opposite-Instrument Hedging** strategy on physical gold perpetual futures contracts.

### Core Philosophy:
1. **Primary Trend Capture**: Rides long-term macro gold moves using completed 4-hour candles.
2. **Adverse Grid Dollar-Cost Averaging**: Captures pullbacks up to 2.5% adverse movement with limit orders.
3. **Opposite-Instrument Hedging for Sideways Alpha**: Exploits favorable run-ups by accumulating independent opposite hedges that recycle small 1% profits, without sacrificing the primary directional position.

---

## 2. Trading Instruments on Delta Exchange India

All trades execute exclusively on Delta Exchange India. We do not assume generic symbols; the bot dynamically discovers and validates these exact instruments:

| Specification | Primary Instrument | Secondary Hedge Instrument |
| :--- | :--- | :--- |
| **Trading Symbol** | `XAUTUSD` | `PAXGUSD` |
| **Product ID** | `131253` | `123006` |
| **Contract Type** | `perpetual_futures` | `perpetual_futures` |
| **Contract Value** | `0.001` oz (~$4.06 per contract) | `0.001` oz (~$4.06 per contract) |
| **Settlement Currency** | `USD` / `USDT` | `USD` / `USDT` |
| **Tick Size** | `0.01` | `0.01` |
| **Role in Strategy** | Directional Trend & Adverse Grids | Opposite-Direction Hedging & 1% TP Harvesting |

> [!NOTE]
> **Why Two Correlated Instruments?**
> Trading primary on `XAUTUSD` and hedges on `PAXGUSD` cleanly separates positions onto distinct contracts, completely avoiding position netting and dual-sided hedge mode limitations on Delta Exchange.

---

## 3. Exact Lot Sizing Rules (Contract-Based)

All trade sizing is strictly **lot-based (contract-based)**. Do NOT convert lots to USD notionals for order sizing:

### A. Main Primary Position (Total 100 Lots of `XAUTUSD`):
- **Initial Entry**: **50 lots** entered via market order immediately when a completed 4-hour candle produces a new Supertrend signal.
- **Grid Limit Orders**: **5 grid levels** of **10 lots each** (total 50 lots) placed at 0.5% adverse spacing:
  - **BULLISH Trend (LONG)**: Limit Buy orders placed below reference price:
    - Level 1: `referencePrice * 0.995` (10 lots)
    - Level 2: `referencePrice * 0.990` (10 lots)
    - Level 3: `referencePrice * 0.985` (10 lots)
    - Level 4: `referencePrice * 0.980` (10 lots)
    - Level 5: `referencePrice * 0.975` (10 lots)
  - **BEARISH Trend (SHORT)**: Limit Sell orders placed above reference price:
    - Level 1: `referencePrice * 1.005` (10 lots)
    - Level 2: `referencePrice * 1.010` (10 lots)
    - Level 3: `referencePrice * 1.015` (10 lots)
    - Level 4: `referencePrice * 1.020` (10 lots)
    - Level 5: `referencePrice * 1.025` (10 lots)
- **Combined Max Primary Size**: Exactly **100 lots** per trend cycle.

### B. Hedge Positions (10 Lots per Hedge on `PAXGUSD`):
- **Trigger**: Every 1.0% favorable movement on the primary instrument from `referencePrice`:
  - If Primary is `LONG`: at +1%, +2%, +3%, etc., open a **10-lot SHORT** hedge on `PAXGUSD`.
  - If Primary is `SHORT`: at -1%, -2%, -3%, etc., open a **10-lot LONG** hedge on `PAXGUSD`.
- **Take-Profit Target**: Each hedge level has an independent **1.0% profit target**:
  - SHORT hedge target: `entryPrice * (1 - 0.01)`
  - LONG hedge target: `entryPrice * (1 + 0.01)`
- **Profit Booking Rule**:
  - When target price is reached, close that specific hedge immediately.
  - Record realized P&L and fees.
  - **CRITICAL**: Never close the primary position simply because a hedge hits take-profit. The primary position stays open to ride the macro trend.

### C. Reversal Protocol:
When a completed 4-hour candle reverses the Supertrend (e.g. from BULLISH to BEARISH):
1. Stop new grid and hedge placements.
2. Cancel all pending grid limit orders.
3. Close all active hedges and realize P&L.
4. Close the previous primary position.
5. Set new `referencePrice` = current market price.
6. Open initial 50 lots in the new direction and deploy 5 new grid orders.

---

## 4. Risk Management Rules

Enforced automatically before every order placement:

```typescript
export interface RiskLimits {
  maxPrimaryLots: 200,      // Max total primary position contracts
  maxHedgeLots: 100,        // Max total aggregate hedge contracts
  maxTotalLots: 300,        // Max total portfolio contracts
  maxDailyLossUsd: 150.0,   // Triggers automatic Emergency Stop if daily loss exceeds $150
  maxDrawdownPercent: 15.0, // Triggers Emergency Stop if drawdown from peak exceeds 15%
  maxGridOrders: 5,         // Max grid levels
  maxOpenHedges: 10,        // Max concurrent open hedge positions
}
```

- **Emergency Stop Activation**:
  - Stops order execution immediately.
  - Cancels all pending grid limit orders.
  - Sets `emergencyStopped = true` and records a critical risk event in SQLite `risk_events`.
- **Clock Drift Protection**:
  - `DeltaAuth` automatically extracts the `Date` HTTP header from Delta Exchange responses to sync `serverTimeOffsetMs`. This prevents local clock drift from causing `401 Unauthorized` errors.
- **Emergency Flatten Protocol**:
  - Available via CLI (`npm run flatten`), REST API (`POST /api/strategy/flatten`), and Dashboard button.
  - Cancels ALL working orders on Delta Exchange India for `XAUTUSD` and `PAXGUSD`.
  - Immediately submits market orders with `reduce_only: true` to close all open positions to 0 contracts.
  - Sets strategy state in SQLite to `KILLED`.
  - Dispatches an emergency alert to Telegram.

---

## 5. Real-Time Telegram Notifications

The bot features a dedicated non-blocking `TelegramService` (`src/monitoring/telegram.ts`) that dispatches rich HTML notifications to Telegram:
- 🚀 **New 4H Supertrend Signal**: Direction, entry price, 50-lot primary entry, and 5 adverse grid orders.
- 📦 **Order Filled**: Primary entries and adverse grid fills with new average price and total contract position.
- 🛡️ **Hedge Opened**: 10 lots on `PAXGUSD` at favorable 1.0% steps with entry price and 1.0% TP target.
- 💰 **Hedge Take-Profit Hit**: Exit price, realized P&L (USD), fees, and confirmation that the primary position is preserved.
- 🔄 **Trend Reversal Rebalancing**: Direction switch, cancellation of pending grids, hedge closures, and new cycle initiation.
- 🚨 **Emergency Flatten**: Reason, cancelled order count, flattened positions, and exposure confirmation.
- ⚠️ **Risk Management Alerts**: Automatic notifications for margin utilization, daily loss limits, and drawdown warnings.

Configuration in `.env`:
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=your_numeric_chat_id
TELEGRAM_ENABLED=true
```

Test utility:
```bash
npm run test:telegram
```

---

## 6. Codebase Map

| Path | Purpose |
| :--- | :--- |
| `src/config/env.ts` | Strict Zod environment variable parsing (`.env`). |
| `src/config/strategy.config.ts` | Strategy parameters and risk limits defaults. |
| `src/database/connection.ts` | SQLite connection via `better-sqlite3` with WAL mode & busy timeout. |
| `src/database/initialize.ts` | Schema migration runner and default strategy seeder. |
| `src/database/migrations/001_init.sql` | 13 relational tables (strategies, orders, positions, grid_levels, hedge_entries, trades, risk_events, etc.). |
| `src/database/repositories/` | Direct prepared statement repositories (no ORM). |
| `src/exchange/delta.auth.ts` | HMAC-SHA256 request signing with dynamic server time sync. |
| `src/exchange/delta.client.ts` | Delta Exchange India REST API client with bulk cancel and market position flattening. |
| `src/exchange/delta.websocket.ts` | Real-time WebSocket connection to `wss://socket.india.delta.exchange`. |
| `src/exchange/products.service.ts` | Dynamic discovery & verification of `XAUTUSD` and `PAXGUSD`. |
| `src/execution/order.executor.ts` | Unified execution layer supporting `PAPER` and `LIVE` trading. |
| `src/execution/order.reconciler.ts` | Background reconciler comparing SQLite with Delta REST state. |
| `src/indicators/atr.ts` | Average True Range using Wilder's RMA over 10 periods. |
| `src/indicators/supertrend.ts` | 4-Hour Supertrend calculation on completed candles. |
| `src/monitoring/alerts.ts` | System alert dispatcher (INFO, WARNING, CRITICAL). |
| `src/monitoring/telegram.ts` | Telegram Bot integration for live trade updates and emergency alerts. |
| `src/strategy/trend.service.ts` | 4H candle evaluator with duplicate signal prevention. |
| `src/strategy/grid.service.ts` | 5-level adverse grid order placement and fill tracking. |
| `src/strategy/hedge.service.ts` | 1% favorable hedge triggers and 1% TP profit booking. |
| `src/strategy/position.manager.ts` | Real-time tracking of primary and hedge positions. |
| `src/strategy/reversal.service.ts` | Order cancellation, position flattening, and cycle transition. |
| `src/strategy/risk.manager.ts` | Pre-trade lot validation and portfolio loss limit checks. |
| `src/strategy/strategy.engine.ts` | Master orchestrator coordinating all strategy services, including `emergencyFlatten()`. |
| `src/api/server.ts` | HTTP REST API (`/api/status`, `/api/strategy/flatten`, etc.) and Live Dark-Themed Web Dashboard. |
| `src/backtest/backtest.engine.ts` | Historical candle simulation engine with fee & slippage modeling. |
| `src/backtest/run-backtest.ts` | Backtest CLI pulling live Delta India 4h historical candles. |
| `scripts/testOrder.ts` | Immediate Open & Close test order script (`npm run test:order --10`). |
| `scripts/testTelegram.ts` | Telegram verification & test notification script (`npm run test:telegram`). |
| `scripts/emergencyFlatten.ts` | Standalone emergency flatten script (`npm run flatten`). |
| `tests/` | Vitest test suite (indicators, calculations, database, risk, telegram-flatten). |

---

## 7. Standard Developer Commands

```bash
# 1. Compile TypeScript (Strict check)
npm run build

# 2. Run unit test suite (30 tests)
npm test

# 3. Test Telegram bot connection and send sample notifications
npm run test:telegram

# 4. Emergency Flatten (Cancels all orders & market closes all positions)
npm run flatten            # Auto-detects LIVE or PAPER from .env
npm run flatten --paper    # Forces local/paper simulation flatten
npm run emergency:flatten  # Alias for npm run flatten

# 5. Run test order script (opens N lots and immediately closes to verify live execution)
npm run test:order --10

# 6. Run backtest with live Delta Exchange candles
npm run backtest

# 7. Start bot in Paper mode
npm start
```

---

## 8. Guidelines for Applying Fixes

When modifying or fixing any part of the codebase:
1. **Never Revert to Dollar Notional Sizing**: All trade sizing must remain strictly in integer lots (contracts).
2. **Preserve Completed Candle Requirement**: Never calculate Supertrend signals on in-progress candles. Only completed candles (`candle.time + 14400 <= nowSec`) are allowed.
3. **Preserve Decimal.js**: Always use `Decimal.js` for financial calculations, P&L formulas, and price rounding.
4. **Always Verify**:
   - Run `npm run build` (`tsc`) to verify type integrity.
   - Run `npm test` to verify all unit tests pass.

