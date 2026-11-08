/**
 * Strategy Mathematical Formulas & Level Calculations
 */

import Decimal from 'decimal.js';
import { Precision } from './precision';
import { GridLevelDefinition, PositionSide, TrendDirection } from '../types/strategy.types';

export class StrategyCalculations {
  /**
   * Calculate Grid entry levels (every 0.5% adverse movement)
   */
  public static calculateGridLevels(
    direction: TrendDirection,
    referencePrice: number,
    gridSpacingPercent: number,
    numLevels: number,
    lotsPerLevel: number,
    contractValue: number = 0.001,
    tickSize: number = 0.01
  ): GridLevelDefinition[] {
    const levels: GridLevelDefinition[] = [];
    const ref = new Decimal(referencePrice);
    const spacing = new Decimal(gridSpacingPercent).dividedBy(100);

    for (let i = 1; i <= numLevels; i++) {
      let priceDec: Decimal;
      if (direction === 'BULLISH') {
        // Adverse for LONG is downward price
        priceDec = ref.times(new Decimal(1).minus(spacing.times(i)));
      } else {
        // Adverse for SHORT is upward price
        priceDec = ref.times(new Decimal(1).plus(spacing.times(i)));
      }

      const price = Precision.roundToTickSize(priceDec, tickSize);
      const quantityContracts = Math.max(1, Math.round(lotsPerLevel));
      const actualNotional = Precision.calculateNotional(quantityContracts, price, contractValue);

      levels.push({
        level: i,
        price,
        quantityContracts,
        notionalUsd: actualNotional,
        status: 'PENDING',
      });
    }

    return levels;
  }

  /**
   * Calculate next hedge trigger level and price
   * Primary LONG -> Every +1% upward moves triggers SHORT hedge
   * Primary SHORT -> Every 1% downward moves triggers LONG hedge
   */
  public static calculateHedgeTriggerPrice(
    primaryDirection: TrendDirection,
    referencePrice: number,
    levelNumber: number,
    hedgeTriggerPercent: number,
    tickSize: number = 0.01
  ): number {
    const ref = new Decimal(referencePrice);
    const triggerPct = new Decimal(hedgeTriggerPercent).dividedBy(100);
    const factor = triggerPct.times(levelNumber);

    let triggerPrice: Decimal;
    if (primaryDirection === 'BULLISH') {
      // Moves up by k * 1%
      triggerPrice = ref.times(new Decimal(1).plus(factor));
    } else {
      // Moves down by k * 1%
      triggerPrice = ref.times(new Decimal(1).minus(factor));
    }

    return Precision.roundToTickSize(triggerPrice, tickSize);
  }

  /**
   * Determine hedge position direction: opposite of primary position
   */
  public static getHedgeSide(primaryDirection: TrendDirection): PositionSide {
    return primaryDirection === 'BULLISH' ? 'SHORT' : 'LONG';
  }

  /**
   * Calculate Take-Profit price for a hedge:
   * SHORT hedge: TP is entryPrice * (1 - TP%)
   * LONG hedge:  TP is entryPrice * (1 + TP%)
   */
  public static calculateHedgeTakeProfitPrice(
    hedgeSide: PositionSide,
    entryPrice: number,
    tpPercent: number = 1.0,
    tickSize: number = 0.01
  ): number {
    const entry = new Decimal(entryPrice);
    const tpDec = new Decimal(tpPercent).dividedBy(100);

    let target: Decimal;
    if (hedgeSide === 'SHORT') {
      // Buy back lower
      target = entry.times(new Decimal(1).minus(tpDec));
    } else {
      // Sell back higher
      target = entry.times(new Decimal(1).plus(tpDec));
    }

    return Precision.roundToTickSize(target, tickSize);
  }
}

