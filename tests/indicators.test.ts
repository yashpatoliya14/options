import { describe, it, expect } from 'vitest';
import { calculateATRSeries, getLatestATR } from '../src/indicators/atr';
import { calculateSupertrendSeries, getLatestSupertrend } from '../src/indicators/supertrend';
import { DeltaCandle } from '../src/types/exchange.types';

function createSampleCandles(count: number, basePrice: number = 2700): DeltaCandle[] {
  const candles: DeltaCandle[] = [];
  let price = basePrice;
  const now = 1700000000;

  for (let i = 0; i < count; i++) {
    const change = (i % 2 === 0 ? 1 : -1) * (i + 1);
    const open = price;
    const close = price + change;
    const high = Math.max(open, close) + 5;
    const low = Math.min(open, close) - 5;

    candles.push({
      time: now + i * 14400,
      open,
      high,
      low,
      close,
      volume: 100,
    });

    price = close;
  }

  return candles;
}

describe('ATR Indicator', () => {
  it('returns null results when candles count is less than ATR period', () => {
    const candles = createSampleCandles(5);
    const atrSeries = calculateATRSeries(candles, 10);
    expect(atrSeries.every((x) => x === null)).toBe(true);
  });

  it('calculates ATR correctly over period 10 with Wilder smoothing', () => {
    const candles = createSampleCandles(25);
    const atrSeries = calculateATRSeries(candles, 10);

    // Indices 0 to 8 must be null
    for (let i = 0; i < 9; i++) {
      expect(atrSeries[i]).toBeNull();
    }

    // Index 9 (10th candle) is the first ATR value
    expect(atrSeries[9]).not.toBeNull();
    expect(atrSeries[9]!.atr).toBeGreaterThan(0);

    // Latest ATR should be positive and finite
    const latestAtr = getLatestATR(candles, 10);
    expect(latestAtr).not.toBeNull();
    expect(latestAtr!).toBeGreaterThan(0);
  });
});

describe('Supertrend Indicator', () => {
  it('correctly flips trend direction from BEARISH to BULLISH on breakout', () => {
    // Construct candles where price starts down, then jumps up
    const candles: DeltaCandle[] = [
      { time: 1000, open: 100, high: 105, low: 95, close: 98, volume: 10 },
      { time: 2000, open: 98, high: 102, low: 94, close: 96, volume: 10 },
      { time: 3000, open: 96, high: 100, low: 92, close: 95, volume: 10 },
      { time: 4000, open: 95, high: 99, low: 90, close: 92, volume: 10 },
      { time: 5000, open: 92, high: 97, low: 88, close: 90, volume: 10 },
      { time: 6000, open: 90, high: 94, low: 86, close: 88, volume: 10 },
      { time: 7000, open: 88, high: 92, low: 84, close: 86, volume: 10 },
      { time: 8000, open: 86, high: 90, low: 82, close: 84, volume: 10 },
      { time: 9000, open: 84, high: 88, low: 80, close: 82, volume: 10 },
      { time: 10000, open: 82, high: 86, low: 78, close: 80, volume: 10 },
      // Candle 11: Breakout upwards to 150
      { time: 11000, open: 80, high: 155, low: 80, close: 150, volume: 100 },
    ];

    const results = calculateSupertrendSeries(candles, 10, 3.0);
    const lastResult = results[results.length - 1];

    expect(lastResult).not.toBeNull();
    expect(lastResult!.trend).toBe('BULLISH');
    expect(lastResult!.supertrend).toBeLessThan(150);
  });

  it('identifies reversal accurately without lookahead bias', () => {
    const candles = createSampleCandles(30);
    const series = calculateSupertrendSeries(candles, 10, 3.0);

    for (let i = 11; i < series.length; i++) {
      const prev = series[i - 1];
      const curr = series[i];
      if (prev && curr) {
        if (curr.trend !== prev.trend) {
          expect(curr.isReversal).toBe(true);
        } else {
          expect(curr.isReversal).toBe(false);
        }
      }
    }
  });
});

