# Workspace Agent Guidelines & Context

This repository is an **Automated Gold Trend + Grid Hedging Trading Bot** for **Delta Exchange India**.

## Primary Rule: Strategy Context
Before diagnosing bugs, modifying strategy logic, or answering questions about the trading algorithm, **ALWAYS review [`STRATEGY_CONTEXT.md`](file:///c:/Users/yashp/Desktop/options/STRATEGY_CONTEXT.md)**.

## Core Invariants:
1. **Lot-Based Sizing**: Sizing is strictly in lots (contracts), NEVER notional USD:
   - Primary trade: 100 lots total of `XAUTUSD` (50 lots initial entry + 5 grids of 10 lots each).
   - Hedges: 10 lots on `PAXGUSD` per 1% favorable trigger level.
2. **Completed Candles Only**: 4-hour Supertrend (`ATR 10, Multiplier 3.0`) evaluates completed candles only.
3. **Single Exchange**: Operates strictly on Delta Exchange India (`https://api.india.delta.exchange`).
4. **Verification**: Always run `npm run build` and `npm test` before concluding any fix.
