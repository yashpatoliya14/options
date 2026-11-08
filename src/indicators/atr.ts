import Decimal from 'decimal.js';
import { DeltaCandle } from '../types/exchange.types';

export interface ATRResult {
  candleTimestamp: number;
  trueRange: number;
  atr: number;
}

/**
 * Calculates Average True Range (ATR) with Wilder's Smoothing (RMA)
 * @param candles Array of historical candles sorted ascending by timestamp
 * @param period Lookback period (default 10)
 * @returns Array of ATR results corresponding to candles from index (period - 1) onwards
 */
export function calculateATRSeries(candles: DeltaCandle[], period: number = 10): (ATRResult | null)[] {
  if (candles.length < period) {
    return candles.map(() => null);
  }

  const results: (ATRResult | null)[] = [];
  const trueRanges: Decimal[] = [];

  // 1. Calculate True Range for every candle
  for (let i = 0; i < candles.length; i++) {
    const high = new Decimal(candles[i].high);
    const low = new Decimal(candles[i].low);

    let tr: Decimal;
    if (i === 0) {
      tr = high.minus(low);
    } else {
      const prevClose = new Decimal(candles[i - 1].close);
      const hl = high.minus(low);
      const hc = high.minus(prevClose).abs();
      const lc = low.minus(prevClose).abs();
      tr = Decimal.max(hl, hc, lc);
    }
    trueRanges.push(tr);
  }

  // 2. Calculate ATR using Wilder's RMA
  let prevAtr: Decimal | null = null;
  const decPeriod = new Decimal(period);

  for (let i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      results.push(null);
      continue;
    }

    if (i === period - 1) {
      // First ATR is the simple average of the first `period` true ranges
      let sum = new Decimal(0);
      for (let j = 0; j < period; j++) {
        sum = sum.plus(trueRanges[j]);
      }
      prevAtr = sum.dividedBy(decPeriod);
      results.push({
        candleTimestamp: candles[i].time,
        trueRange: trueRanges[i].toNumber(),
        atr: prevAtr.toNumber(),
      });
    } else {
      // Wilder's smoothing: ATR = (prevATR * (period - 1) + currentTR) / period
      const currentTR = trueRanges[i];
      const currentAtr = (prevAtr!.times(decPeriod.minus(1))).plus(currentTR).dividedBy(decPeriod);
      prevAtr = currentAtr;
      results.push({
        candleTimestamp: candles[i].time,
        trueRange: currentTR.toNumber(),
        atr: currentAtr.toNumber(),
      });
    }
  }

  return results;
}

/**
 * Convenience function to get the latest completed ATR value
 */
export function getLatestATR(candles: DeltaCandle[], period: number = 10): number | null {
  const series = calculateATRSeries(candles, period);
  if (series.length === 0) return null;
  const last = series[series.length - 1];
  return last ? last.atr : null;
}

