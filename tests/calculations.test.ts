import { describe, it, expect } from 'vitest';
import { Precision } from '../src/utils/precision';
import { StrategyCalculations } from '../src/utils/calculations';

describe('Precision and Financial Arithmetic', () => {
  it('rounds prices accurately to tick size 0.01', () => {
    expect(Precision.roundToTickSize(2750.1249, 0.01)).toBe(2750.12);
    expect(Precision.roundToTickSize(2750.1251, 0.01)).toBe(2750.13);
    expect(Precision.roundToTickSize(100.555, 0.05)).toBe(100.55);
  });

  it('calculates contract quantity from USD notional correctly', () => {
    // Notional = contracts * contractValue * price
    // e.g. $500 notional at price $4000/oz with 0.001 contractValue (1 contract = 0.001 oz = $4.00)
    // 500 / 4 = 125 contracts
    const contracts = Precision.calculateContractsFromNotional(500, 4000, 0.001);
    expect(contracts).toBe(125);

    // Exact notional calculation
    const exactNotional = Precision.calculateNotional(contracts, 4000, 0.001);
    expect(exactNotional).toBe(500);
  });

  it('calculates Unrealized and Realized P&L for LONG positions', () => {
    // 100 contracts @ $2000, mark price $2100 (+100 price move)
    // PnL = 100 * 0.001 * 100 = $10.00 profit
    const pnlProfit = Precision.calculatePnl('LONG', 2000, 2100, 100, 0.001);
    expect(pnlProfit).toBe(10);

    // Loss: mark price $1950 (-50 price move)
    // PnL = 100 * 0.001 * (-50) = -$5.00 loss
    const pnlLoss = Precision.calculatePnl('LONG', 2000, 1950, 100, 0.001);
    expect(pnlLoss).toBe(-5);
  });

  it('calculates Unrealized and Realized P&L for SHORT positions', () => {
    // 100 contracts @ $2000, mark price $1900 (-100 price move)
    // SHORT profit = 100 * 0.001 * (2000 - 1900) = $10.00
    const pnlProfit = Precision.calculatePnl('SHORT', 2000, 1900, 100, 0.001);
    expect(pnlProfit).toBe(10);

    // Loss: mark price $2050 (+50 price move)
    // SHORT loss = 100 * 0.001 * (2000 - 2050) = -$5.00
    const pnlLoss = Precision.calculatePnl('SHORT', 2000, 2050, 100, 0.001);
    expect(pnlLoss).toBe(-5);
  });
});

describe('Strategy Level Calculations', () => {
  it('generates 5 adverse grid levels at 0.5% spacing for BULLISH (LONG) trend', () => {
    const referencePrice = 100000;
    const levels = StrategyCalculations.calculateGridLevels(
      'BULLISH',
      referencePrice,
      0.5,
      5,
      10, // 10 lots per level
      0.001,
      0.01
    );

    expect(levels).toHaveLength(5);
    expect(levels[0].quantityContracts).toBe(10);
    // Grid 1: 99,500 (-0.5%)
    expect(levels[0].price).toBe(99500);
    // Grid 2: 99,000 (-1.0%)
    expect(levels[1].price).toBe(99000);
    // Grid 3: 98,500 (-1.5%)
    expect(levels[2].price).toBe(98500);
    // Grid 4: 98,000 (-2.0%)
    expect(levels[3].price).toBe(98000);
    // Grid 5: 97,500 (-2.5%)
    expect(levels[4].price).toBe(97500);

    expect(levels.every((l) => l.quantityContracts > 0)).toBe(true);
  });

  it('generates 5 adverse grid levels at 0.5% spacing for BEARISH (SHORT) trend', () => {
    const referencePrice = 100000;
    const levels = StrategyCalculations.calculateGridLevels(
      'BEARISH',
      referencePrice,
      0.5,
      5,
      10, // 10 lots per level
      0.001,
      0.01
    );

    expect(levels).toHaveLength(5);
    expect(levels[0].quantityContracts).toBe(10);
    // Grid 1: 100,500 (+0.5%)
    expect(levels[0].price).toBe(100500);
    // Grid 2: 101,000 (+1.0%)
    expect(levels[1].price).toBe(101000);
    // Grid 3: 101,500 (+1.5%)
    expect(levels[2].price).toBe(101500);
    // Grid 4: 102,000 (+2.0%)
    expect(levels[3].price).toBe(102000);
    // Grid 5: 102,500 (+2.5%)
    expect(levels[4].price).toBe(102500);
  });

  it('calculates hedge trigger prices for favorable 1% movements', () => {
    // Primary BULLISH (+1% per level)
    const t1 = StrategyCalculations.calculateHedgeTriggerPrice('BULLISH', 100000, 1, 1.0, 0.01);
    expect(t1).toBe(101000);

    const t2 = StrategyCalculations.calculateHedgeTriggerPrice('BULLISH', 100000, 2, 1.0, 0.01);
    expect(t2).toBe(102000);

    // Primary BEARISH (-1% per level)
    const b1 = StrategyCalculations.calculateHedgeTriggerPrice('BEARISH', 100000, 1, 1.0, 0.01);
    expect(b1).toBe(99000);

    const b2 = StrategyCalculations.calculateHedgeTriggerPrice('BEARISH', 100000, 2, 1.0, 0.01);
    expect(b2).toBe(98000);
  });

  it('calculates individual 1% hedge take-profit targets correctly', () => {
    // SHORT hedge entered at 101,000 -> TP is 1% below entry = 99,990
    const shortTp = StrategyCalculations.calculateHedgeTakeProfitPrice('SHORT', 101000, 1.0, 0.01);
    expect(shortTp).toBe(99990);

    // LONG hedge entered at 99,000 -> TP is 1% above entry = 99,990
    const longTp = StrategyCalculations.calculateHedgeTakeProfitPrice('LONG', 99000, 1.0, 0.01);
    expect(longTp).toBe(99990);
  });
});

