import Decimal from 'decimal.js';
import { DeltaCandle } from '../types/exchange.types';
import { SupertrendResult, TrendDirection } from '../types/strategy.types';
import { calculateATRSeries } from './atr';

export interface FullSupertrendPoint extends SupertrendResult {
  hl2: number;
  basicUpperBand: number;
  basicLowerBand: number;
}

/**
 * Calculates Supertrend indicator over a series of completed candles
 * @param candles Array of historical candles sorted ascending by timestamp
 * @param period ATR period (default 10)
 * @param multiplier ATR multiplier (default 3.0)
 * @returns Array of Supertrend results matching candles (null for candles before period)
 */
export function calculateSupertrendSeries(
  candles: DeltaCandle[],
  period: number = 10,
  multiplier: number = 3.0
): (FullSupertrendPoint | null)[] {
  if (candles.length < period) {
    return candles.map(() => null);
  }

  const atrSeries = calculateATRSeries(candles, period);
  const results: (FullSupertrendPoint | null)[] = [];
  const decMultiplier = new Decimal(multiplier);

  let prevFinalUpper = new Decimal(0);
  let prevFinalLower = new Decimal(0);
  let prevTrend: TrendDirection = 'NEUTRAL';
  let prevSupertrend = new Decimal(0);

  for (let i = 0; i < candles.length; i++) {
    const atrPoint = atrSeries[i];
    if (!atrPoint) {
      results.push(null);
      continue;
    }

    const candle = candles[i];
    const high = new Decimal(candle.high);
    const low = new Decimal(candle.low);
    const close = new Decimal(candle.close);
    const hl2 = high.plus(low).dividedBy(2);
    const atr = new Decimal(atrPoint.atr);

    const basicUpper = hl2.plus(decMultiplier.times(atr));
    const basicLower = hl2.minus(decMultiplier.times(atr));

    let finalUpper: Decimal;
    let finalLower: Decimal;
    let currentTrend: TrendDirection;
    let currentSupertrend: Decimal;
    let isReversal = false;

    if (prevTrend === 'NEUTRAL') {
      // Initialization at the first calculated candle
      finalUpper = basicUpper;
      finalLower = basicLower;
      currentTrend = close.greaterThanOrEqualTo(finalLower) ? 'BULLISH' : 'BEARISH';
      currentSupertrend = currentTrend === 'BULLISH' ? finalLower : finalUpper;
      isReversal = false;
    } else {
      const prevClose = new Decimal(candles[i - 1].close);

      // Final Upper Band
      if (basicUpper.lessThan(prevFinalUpper) || prevClose.greaterThan(prevFinalUpper)) {
        finalUpper = basicUpper;
      } else {
        finalUpper = prevFinalUpper;
      }

      // Final Lower Band
      if (basicLower.greaterThan(prevFinalLower) || prevClose.lessThan(prevFinalLower)) {
        finalLower = basicLower;
      } else {
        finalLower = prevFinalLower;
      }

      // Determine Trend
      if (prevTrend === 'BEARISH') {
        if (close.greaterThan(finalUpper)) {
          currentTrend = 'BULLISH';
          currentSupertrend = finalLower;
          isReversal = true;
        } else {
          currentTrend = 'BEARISH';
          currentSupertrend = finalUpper;
          isReversal = false;
        }
      } else {
        // prevTrend was 'BULLISH'
        if (close.lessThan(finalLower)) {
          currentTrend = 'BEARISH';
          currentSupertrend = finalUpper;
          isReversal = true;
        } else {
          currentTrend = 'BULLISH';
          currentSupertrend = finalLower;
          isReversal = false;
        }
      }
    }

    prevFinalUpper = finalUpper;
    prevFinalLower = finalLower;
    prevTrend = currentTrend;
    prevSupertrend = currentSupertrend;

    results.push({
      candleTimestamp: candle.time,
      trend: currentTrend,
      supertrend: currentSupertrend.toNumber(),
      upperBand: finalUpper.toNumber(),
      lowerBand: finalLower.toNumber(),
      atr: atrPoint.atr,
      isReversal,
      hl2: hl2.toNumber(),
      basicUpperBand: basicUpper.toNumber(),
      basicLowerBand: basicLower.toNumber(),
    });
  }

  return results;
}

/**
 * Returns the Supertrend calculation for the latest completed candle
 */
export function getLatestSupertrend(
  candles: DeltaCandle[],
  period: number = 10,
  multiplier: number = 3.0
): FullSupertrendPoint | null {
  const series = calculateSupertrendSeries(candles, period, multiplier);
  if (series.length === 0) return null;
  return series[series.length - 1];
}

