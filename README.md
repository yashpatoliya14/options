# Automated Gold Trend + Grid Hedging Trading Bot (Delta Exchange India)

A production-grade, quantitative algorithmic trading bot engineered for **Delta Exchange India** (`https://api.india.delta.exchange`), implementing a high-capacity **4-Hour Supertrend + Grid Averaging + Dynamic Opposite-Instrument Hedging** strategy.

---

## 📌 Strategy Architecture

The strategy combines trend-following directional momentum on physical gold contracts with adverse grid dollar-cost averaging and independent opposite-instrument hedging for sideways market alpha.

```
       [ 4-Hour Completed Candle ]
                   │
         [ Supertrend Indicator ]
          (ATR: 10, Multiplier: 3)
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
    [ BULLISH ]             [ BEARISH ]
       │                       │
 50% Initial Entry       50% Initial Entry
  (LONG $500 XAUT)        (SHORT $500 XAUT)
       │                       │
  5 Adverse Grids         5 Adverse Grids
   (-0.5% each)            (+0.5% each)
       │                       │
       └───────────┬───────────┘
                   │
       [ Real-Time Price Monitor ]
                   │
    ┌──────────────┴──────────────┐
    ▼                             ▼
Adverse Movement            Favorable Movement (+1% steps)
(Triggers Grid Orders)      (Triggers Opposite Hedge in PAXGUSD)
                                  │
                       [ Independent 1% Take-Profit ]
                       (Book Profit & Keep Primary Open)
```

### 1. Delta Exchange India Instruments
Dynamically verified and discovered via Delta India REST API (`/v2/products`):
- **Primary Trend Instrument**: `XAUTUSD` (Product ID: `131253`)
  - Perpetual futures contract settled in USD.
  - Contract Value: `0.001` oz (~$4.00 per contract).
  - Tick size: `0.01`.
- **Secondary Hedge Instrument**: `PAXGUSD` (Product ID: `123006`)
  - Perpetual futures contract settled in USD.
  - Contract Value: `0.001` oz (~$4.00 per contract).
  - Tick size: `0.01`.

*Note: Dual-instrument hedging cleanly separates primary and hedge positions onto distinct contracts, eliminating position-netting conflicts on Delta Exchange.*

### 2. Five Core Strategy Components
1. **4-Hour Supertrend Trend Detection**:
   - Calculated strictly on completed 4h candles (`ATR 10`, `Multiplier 3.0`).
   - Prevents duplicate signals by persisting `last_processed_candle` timestamp in SQLite.
   - Zero lookahead bias.
2. **50% Initial Position Entry**:
   - Total planned primary notional: $1,000.
   - On new Supertrend signal: opens 50% ($500 notional) immediately.
   - Reserves 50% ($500) for grid limit orders.
3. **5-Level Adverse Grid System**:
   - Places limit orders every 0.5% adverse movement ($100 per grid level).
   - LONG: `ref * (1 - 0.005 * i)` (e.g. 99,500, 99,000, 98,500, 98,000, 97,500).
   - SHORT: `ref * (1 + 0.005 * i)` (e.g. 100,500, 101,000, 101,500, 102,000, 102,500).
   - Recalculates weighted average entry price upon fill.
4. **Opposite-Instrument Hedging (Every +1% Favorable Movement)**:
   - When Primary moves favorably by every 1%, opens an opposite-direction hedge in `PAXGUSD`.
   - Primary LONG -> Opens SHORT hedge on `PAXGUSD` ($100 notional).
   - Primary SHORT -> Opens LONG hedge on `PAXGUSD` ($100 notional).
   - Idempotent tracking per level ($1, 2, 3, \dots$) prevents duplicate entries.
5. **Independent 1% Hedge Profit Booking**:
   - Each hedge has an independent 1% take-profit target:
     - SHORT hedge TP: `entryPrice * (1 - 0.01)`
     - LONG hedge TP: `entryPrice * (1 + 0.01)`
   - When reached: closes the hedge, books realized P&L, records fees, and **keeps the primary position open**.
6. **Supertrend Reversal Protocol**:
   - Cancels all pending grid limit orders.
   - Closes open hedges and books P&L.
   - Closes previous primary position.
   - Resets reference price and begins the new trend cycle in the opposite direction.

---

## 🛡️ Risk Management System

Rigorous safety controls enforced before every order:
- **Max Primary Notional**: $2,000 cap.
- **Max Hedge Notional**: $1,000 cap.
- **Max Total Exposure**: $3,000 cap.
- **Max Daily Loss**: $150 limit (triggers automatic Emergency Stop).
- **Max Drawdown**: 15% limit (triggers Emergency Stop).
- **Max Margin Utilization**: 70% threshold.
- **Max Open Hedges**: 10 simultaneous hedges.
- **Automated REST Reconciliation**: Synchronizes SQLite orders and positions with Delta Exchange.

---

## 🛠️ Technology Stack

- **Runtime**: Node.js + TypeScript (Strict Mode)
- **Exchange API**: Delta Exchange India REST API & WebSocket API (`wss://socket.india.delta.exchange`)
- **Database**: SQLite via `better-sqlite3` (WAL mode enabled, prepared statements, zero ORM bloat)
- **Financial Math**: `Decimal.js` (28-digit precision, no floating-point rounding errors)
- **Validation**: `Zod`
- **Testing**: `Vitest`
- **Logging**: `Pino` with `pino-pretty`

---

## 🚀 Getting Started

### 1. Installation
```bash
npm install
```

### 2. Environment Configuration
Copy `.env.example` to `.env` and configure your credentials:
```env
DELTA_API_KEY=your_delta_india_api_key
DELTA_API_SECRET=your_delta_india_api_secret
DELTA_BASE_URL=https://api.india.delta.exchange
DELTA_WS_URL=wss://socket.india.delta.exchange

TRADING_MODE=PAPER # Options: PAPER | LIVE | BACKTEST
```

### 3. Run the Backtest
Pulls historical 4h candles from Delta Exchange India and runs the full simulation:
```bash
npm run backtest
```

### 4. Run the Unit Test Suite
Executes 22 automated unit tests across indicators, arithmetic, database, and risk controls:
```bash
npm test
```

### 5. Build and Start the Trading Bot
```bash
# Development (with tsx hot-reloading)
npm start

# Or compile to JavaScript and run in production
npm run build
npm run prod
```

---

## 📊 Web Dashboard & REST API

The bot includes an embedded HTTP server and real-time WebSocket dashboard accessible at:
```
http://localhost:3000
```

### REST Endpoints:
- `GET /api/status`: Overall engine status, current trend direction, uptime.
- `GET /api/strategy`: Full strategy state, configuration, and portfolio snapshot.
- `GET /api/positions`: Primary and open hedge positions.
- `GET /api/orders`: Active grid limit orders and recent executions.
- `GET /api/trades`: Trade history with realized PnL and fees.
- `GET /api/performance`: Daily and cumulative metrics.
- `GET /api/risk`: Risk limits, exposure, margin utilization, and risk events.

### Control Endpoints:
- `POST /api/strategy/start`: Starts engine and WebSocket feed.
- `POST /api/strategy/pause`: Pauses order generation.
- `POST /api/strategy/resume`: Resumes paused strategy.
- `POST /api/strategy/stop`: Gracefully stops and cancels pending orders.
- `POST /api/strategy/kill`: Immediate emergency kill switch.
