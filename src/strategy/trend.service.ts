import { DeltaClient } from '../exchange/delta.client';
import { StrategyRepository } from '../database/repositories/strategy.repository';
import { calculateSupertrendSeries, FullSupertrendPoint } from '../indicators/supertrend';
import { DeltaCandle } from '../types/exchange.types';
import { TrendDirection } from '../types/strategy.types';
import { logger } from '../monitoring/logger';

export interface TrendCheckResult {
  hasNewSignal: boolean;
  isReversal: boolean;
  direction: TrendDirection;
  previousDirection: TrendDirection;
  supertrend: FullSupertrendPoint | null;
  latestCompletedCandle: DeltaCandle | null;
}

export class TrendService {
  private deltaClient: DeltaClient;
  private strategyRepo: StrategyRepository;
  private strategyId: string;
  private primarySymbol: string;
  private timeframe: string;
  private atrPeriod: number;
  private atrMultiplier: number;

  constructor(
    deltaClient: DeltaClient,
    strategyRepo: StrategyRepository,
    strategyId: string,
    primarySymbol: string = 'XAUTUSD',
    timeframe: string = '4h',
    atrPeriod: number = 10,
    atrMultiplier: number = 3.0
  ) {
    this.deltaClient = deltaClient;
    this.strategyRepo = strategyRepo;
    this.strategyId = strategyId;
    this.primarySymbol = primarySymbol;
    this.timeframe = timeframe;
    this.atrPeriod = atrPeriod;
    this.atrMultiplier = atrMultiplier;
  }

  /**
   * Check 4-hour trend based exclusively on completed candles.
   * Prevents duplicate signals and lookahead bias.
   */
  public async checkTrend(): Promise<TrendCheckResult> {
    const currentState = this.strategyRepo.getState(this.strategyId);
    const lastProcessedTimestamp = currentState?.lastProcessedCandleTimestamp || 0;
    const currentDirection = currentState?.currentDirection || 'NEUTRAL';

    // 1. Fetch historical candles from Delta Exchange
    // 4h resolution = 14400 seconds. 50 candles = 50 * 14400 = 720,000s (~8.3 days)
    const nowSec = Math.floor(Date.now() / 1000);
    const candleDurationSec = this.getCandleDurationSec(this.timeframe);
    const startSec = nowSec - (this.atrPeriod + 50) * candleDurationSec;

    let candles: DeltaCandle[] = [];
    try {
      candles = await this.deltaClient.getCandles(this.primarySymbol, this.timeframe, startSec, nowSec);
    } catch (err: any) {
      logger.error({ err: err.message, symbol: this.primarySymbol }, 'Failed to fetch candles from Delta');
      return {
        hasNewSignal: false,
        isReversal: false,
        direction: currentDirection,
        previousDirection: currentDirection,
        supertrend: null,
        latestCompletedCandle: null,
      };
    }

    if (candles.length < this.atrPeriod + 2) {
      logger.warn({ count: candles.length, required: this.atrPeriod + 2 }, 'Insufficient candles for Supertrend');
      return {
        hasNewSignal: false,
        isReversal: false,
        direction: currentDirection,
        previousDirection: currentDirection,
        supertrend: null,
        latestCompletedCandle: null,
      };
    }

    // Filter to strictly COMPLETED candles:
    // A candle starting at `candle.time` is completed when `candle.time + candleDurationSec <= nowSec`
    const completedCandles = candles.filter((c) => c.time + candleDurationSec <= nowSec);

    if (completedCandles.length < this.atrPeriod + 2) {
      return {
        hasNewSignal: false,
        isReversal: false,
        direction: currentDirection,
        previousDirection: currentDirection,
        supertrend: null,
        latestCompletedCandle: null,
      };
    }

    // Save completed candles to database cache
    for (const c of completedCandles.slice(-10)) {
      this.strategyRepo.saveCandle({
        symbol: this.primarySymbol,
        timeframe: this.timeframe,
        timestamp: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
        isClosed: true,
      });
    }

    // 2. Compute Supertrend series
    const supertrendSeries = calculateSupertrendSeries(
      completedCandles,
      this.atrPeriod,
      this.atrMultiplier
    );

    const latestPoint = supertrendSeries[supertrendSeries.length - 1];
    const latestCandle = completedCandles[completedCandles.length - 1];

    if (!latestPoint || !latestCandle) {
      return {
        hasNewSignal: false,
        isReversal: false,
        direction: currentDirection,
        previousDirection: currentDirection,
        supertrend: null,
        latestCompletedCandle: null,
      };
    }

    // 3. Prevent duplicate signals by comparing with last processed candle timestamp
    if (latestCandle.time <= lastProcessedTimestamp) {
      // Already evaluated this completed candle
      return {
        hasNewSignal: false,
        isReversal: false,
        direction: currentDirection,
        previousDirection: currentDirection,
        supertrend: latestPoint,
        latestCompletedCandle: latestCandle,
      };
    }

    // 4. Determine if there is a new signal or trend reversal
    const newDirection = latestPoint.trend;
    const isReversal = currentDirection !== 'NEUTRAL' && newDirection !== currentDirection;
    const isInitialSignal = currentDirection === 'NEUTRAL' && newDirection !== 'NEUTRAL';
    const hasNewSignal = isReversal || isInitialSignal;

    // Update last processed timestamp in DB
    this.strategyRepo.updateState(this.strategyId, {
      lastProcessedCandleTimestamp: latestCandle.time,
    });

    logger.info(
      {
        candleTime: new Date(latestCandle.time * 1000).toISOString(),
        close: latestCandle.close,
        supertrend: latestPoint.supertrend,
        direction: newDirection,
        previousDirection: currentDirection,
        isReversal,
        hasNewSignal,
      },
      'Evaluated completed 4h candle for Supertrend'
    );

    return {
      hasNewSignal,
      isReversal,
      direction: newDirection,
      previousDirection: currentDirection,
      supertrend: latestPoint,
      latestCompletedCandle: latestCandle,
    };
  }

  private getCandleDurationSec(timeframe: string): number {
    switch (timeframe) {
      case '1m': return 60;
      case '5m': return 300;
      case '15m': return 900;
      case '1h': return 3600;
      case '4h': return 14400;
      case '1d': return 86400;
      default: return 14400;
    }
  }
}

