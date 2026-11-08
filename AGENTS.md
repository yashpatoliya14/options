# Agent Guidelines: Delta Gold Trend + Grid Hedging Bot

When instructed to fix, modify, or inspect this codebase:
1. Immediately read [`STRATEGY_CONTEXT.md`](file:///c:/Users/yashp/Desktop/options/STRATEGY_CONTEXT.md) for complete strategy specifications, exchange rules, and lot sizing.
2. Maintain lot-based execution (Primary: 100 lots total = 50 initial + 5x10 grids on `XAUTUSD`; Hedges: 10 lots on `PAXGUSD`).
3. Verify fixes with `npm run build` and `npm test`.

