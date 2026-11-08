import Decimal from 'decimal.js';
import { DeltaCandle } from '../types/exchange.types';
import { calculateSupertrendSeries } from '../indicators/supertrend';
import { StrategyCalculations } from '../utils/calculations';
import { Precision } from '../utils/precision';
import { BacktestTrade, BacktestReport, PerformanceCalculator } from './performance.metrics';
import { PositionSide, TrendDirection } from '../types/strategy.types';

export interface BacktestParams {
  initialCapitalUsd?: number;
  primaryTotalLots?: number;
  primaryInitialLots?: number;
  gridSpacingPercent?: number;
  gridLevelsCount?: number;
  gridLotsPerLevel?: number;
  hedgeTriggerPercent?: number;
  hedgeTakeProfitPercent?: number;
  hedgeLots?: number;
  atrPeriod?: number;
  atrMultiplier?: number;
  takerFeeRate?: number;
  makerFeeRate?: number;
  contractValue?: number;
  tickSize?: number;
}

interface SimulatedHedge {
  level: number;
  side: PositionSide;
  entryPrice: number;
  targetPrice: number;
  quantityContracts: number;
  entryTimestamp: number;
  notionalUsd: number;
}

interface SimulatedGrid {
  level: number;
  price: number;
  quantityContracts: number;
  filled: boolean;
}

export class BacktestEngine {
  private params: Required<BacktestParams>;

  constructor(params?: BacktestParams) {
    this.params = {
      initialCapitalUsd: params?.initialCapitalUsd ?? 2000,
      primaryTotalLots: params?.primaryTotalLots ?? 100,
      primaryInitialLots: params?.primaryInitialLots ?? 50,
      gridSpacingPercent: params?.gridSpacingPercent ?? 0.5,
      gridLevelsCount: params?.gridLevelsCount ?? 5,
      gridLotsPerLevel: params?.gridLotsPerLevel ?? 10,
      hedgeTriggerPercent: params?.hedgeTriggerPercent ?? 1.0,
      hedgeTakeProfitPercent: params?.hedgeTakeProfitPercent ?? 1.0,
      hedgeLots: params?.hedgeLots ?? 10,
      atrPeriod: params?.atrPeriod ?? 10,
      atrMultiplier: params?.atrMultiplier ?? 3.0,
      takerFeeRate: params?.takerFeeRate ?? 0.0005,
      makerFeeRate: params?.makerFeeRate ?? 0.0002,
      contractValue: params?.contractValue ?? 0.001,
      tickSize: params?.tickSize ?? 0.01,
    };
  }

  public run(candles: DeltaCandle[]): { report: BacktestReport; trades: BacktestTrade[]; equityCurve: any[] } {
    if (candles.length < this.params.atrPeriod + 5) {
      throw new Error(`Insufficient candles for backtest. Need at least ${this.params.atrPeriod + 5}`);
    }

    const supertrendSeries = calculateSupertrendSeries(
      candles,
      this.params.atrPeriod,
      this.params.atrMultiplier
    );

    const trades: BacktestTrade[] = [];
    const equityCurve: { timestamp: number; equity: number }[] = [];
    let currentEquity = this.params.initialCapitalUsd;

    // Active cycle state
    let activeDirection: TrendDirection = 'NEUTRAL';
    let referencePrice = 0;
    let primarySide: PositionSide = 'LONG';
    let primaryContracts = 0;
    let primaryAvgPrice = 0;
    let primaryEntryTimestamp = 0;

    let activeGrids: SimulatedGrid[] = [];
    let activeHedges: SimulatedHedge[] = [];
    let triggeredHedgeLevels = new Set<number>();

    const startIndex = this.params.atrPeriod;

    for (let i = startIndex; i < candles.length; i++) {
      const candle = candles[i];
      const stPoint = supertrendSeries[i];
      if (!stPoint) continue;

      const newTrend = stPoint.trend;

      // 1. Check for Supertrend Reversal
      if (activeDirection !== 'NEUTRAL' && newTrend !== activeDirection) {
        // Close Primary Position
        if (primaryContracts > 0) {
          const exitPrice = candle.open;
          const pnl = Precision.calculatePnl(
            primarySide,
            primaryAvgPrice,
            exitPrice,
            primaryContracts,
            this.params.contractValue
          );
          const notional = Precision.calculateNotional(primaryContracts, exitPrice, this.params.contractValue);
          const fees = notional * this.params.takerFeeRate;
          const netPnl = pnl - fees;

          currentEquity += netPnl;

          trades.push({
            id: `primary-rev-${i}`,
            symbol: 'XAUTUSD',
            side: primarySide,
            entryPrice: primaryAvgPrice,
            exitPrice,
            quantityContracts: primaryContracts,
            notionalUsd: notional,
            realizedPnl: pnl,
            fees,
            netPnl,
            entryTimestamp: primaryEntryTimestamp,
            exitTimestamp: candle.time,
            purpose: 'PRIMARY_REVERSAL',
          });

          primaryContracts = 0;
          primaryAvgPrice = 0;
        }

        // Close all open hedges on reversal
        for (const h of activeHedges) {
          const exitPrice = candle.open;
          const pnl = Precision.calculatePnl(
            h.side,
            h.entryPrice,
            exitPrice,
            h.quantityContracts,
            this.params.contractValue
          );
          const notional = Precision.calculateNotional(h.quantityContracts, exitPrice, this.params.contractValue);
          const fees = notional * this.params.takerFeeRate;
          const netPnl = pnl - fees;

          currentEquity += netPnl;

          trades.push({
            id: `hedge-rev-${h.level}-${i}`,
            symbol: 'PAXGUSD',
            side: h.side,
            entryPrice: h.entryPrice,
            exitPrice,
            quantityContracts: h.quantityContracts,
            notionalUsd: notional,
            realizedPnl: pnl,
            fees,
            netPnl,
            entryTimestamp: h.entryTimestamp,
            exitTimestamp: candle.time,
            purpose: 'HEDGE_REVERSAL',
          });
        }

        activeHedges = [];
        activeGrids = [];
        triggeredHedgeLevels.clear();
        activeDirection = 'NEUTRAL';
      }

      // 2. Initial Entry on New Trend
      if (activeDirection === 'NEUTRAL' && newTrend !== 'NEUTRAL') {
        activeDirection = newTrend;
        primarySide = newTrend === 'BULLISH' ? 'LONG' : 'SHORT';
        referencePrice = candle.open;
        primaryEntryTimestamp = candle.time;

        primaryContracts = this.params.primaryInitialLots;
        primaryAvgPrice = referencePrice;

        const initialNotional = Precision.calculateNotional(primaryContracts, referencePrice, this.params.contractValue);
        const entryFees = initialNotional * this.params.takerFeeRate;
        currentEquity -= entryFees;

        // Setup 5 Grid levels
        const gridDefs = StrategyCalculations.calculateGridLevels(
          activeDirection,
          referencePrice,
          this.params.gridSpacingPercent,
          this.params.gridLevelsCount,
          this.params.gridLotsPerLevel,
          this.params.contractValue,
          this.params.tickSize
        );

        activeGrids = gridDefs.map((g) => ({
          level: g.level,
          price: g.price,
          quantityContracts: g.quantityContracts,
          filled: false,
        }));
      }

      // 3. Intraday candle processing (grids, hedges, take-profits)
      if (activeDirection !== 'NEUTRAL' && primaryContracts > 0) {
        // A. Check Pending Grid Fills
        for (const g of activeGrids) {
          if (g.filled) continue;

          let filled = false;
          if (primarySide === 'LONG' && candle.low <= g.price) {
            filled = true;
          } else if (primarySide === 'SHORT' && candle.high >= g.price) {
            filled = true;
          }

          if (filled) {
            g.filled = true;
            const addedNotional = g.quantityContracts * g.price * this.params.contractValue;
            const existingNotional = primaryContracts * primaryAvgPrice * this.params.contractValue;
            const totalContracts = primaryContracts + g.quantityContracts;

            primaryAvgPrice = (existingNotional + addedNotional) / (totalContracts * this.params.contractValue);
            primaryContracts = totalContracts;

            const fee = addedNotional * this.params.makerFeeRate;
            currentEquity -= fee;
          }
        }

        // B. Check Hedge Take-Profits
        const remainingHedges: SimulatedHedge[] = [];
        for (const h of activeHedges) {
          let tpFilled = false;
          if (h.side === 'SHORT' && candle.low <= h.targetPrice) {
            tpFilled = true;
          } else if (h.side === 'LONG' && candle.high >= h.targetPrice) {
            tpFilled = true;
          }

          if (tpFilled) {
            const exitPrice = h.targetPrice;
            const pnl = Precision.calculatePnl(
              h.side,
              h.entryPrice,
              exitPrice,
              h.quantityContracts,
              this.params.contractValue
            );
            const notional = Precision.calculateNotional(h.quantityContracts, exitPrice, this.params.contractValue);
            const fee = notional * this.params.makerFeeRate;
            const netPnl = pnl - fee;

            currentEquity += netPnl;

            trades.push({
              id: `hedge-tp-${h.level}-${i}`,
              symbol: 'PAXGUSD',
              side: h.side,
              entryPrice: h.entryPrice,
              exitPrice,
              quantityContracts: h.quantityContracts,
              notionalUsd: notional,
              realizedPnl: pnl,
              fees: fee,
              netPnl,
              entryTimestamp: h.entryTimestamp,
              exitTimestamp: candle.time,
              purpose: `HEDGE_TP_LEVEL_${h.level}`,
            });
          } else {
            remainingHedges.push(h);
          }
        }
        activeHedges = remainingHedges;

        // C. Check New Hedge Triggers (every 1% favorable movement)
        const ref = referencePrice;
        let favorablePeak = 0;
        if (primarySide === 'LONG') {
          favorablePeak = (candle.high - ref) / ref;
        } else {
          favorablePeak = (ref - candle.low) / ref;
        }

        if (favorablePeak >= 0.01) {
          const maxLevelReached = Math.floor(favorablePeak / (this.params.hedgeTriggerPercent / 100));

          for (let lvl = 1; lvl <= maxLevelReached; lvl++) {
            if (!triggeredHedgeLevels.has(lvl)) {
              triggeredHedgeLevels.add(lvl);

              const hedgeSide: PositionSide = primarySide === 'LONG' ? 'SHORT' : 'LONG';
              const hedgePrice = primarySide === 'LONG'
                ? ref * (1 + 0.01 * lvl)
                : ref * (1 - 0.01 * lvl);

              const hedgeContracts = this.params.hedgeLots;

              const tpPrice = StrategyCalculations.calculateHedgeTakeProfitPrice(
                hedgeSide,
                hedgePrice,
                this.params.hedgeTakeProfitPercent,
                this.params.tickSize
              );

              const notional = Precision.calculateNotional(hedgeContracts, hedgePrice, this.params.contractValue);
              const fee = notional * this.params.takerFeeRate;
              currentEquity -= fee;

              activeHedges.push({
                level: lvl,
                side: hedgeSide,
                entryPrice: hedgePrice,
                targetPrice: tpPrice,
                quantityContracts: hedgeContracts,
                entryTimestamp: candle.time,
                notionalUsd: notional,
              });
            }
          }
        }
      }

      // 4. Mark equity curve
      let unPnlPrimary = 0;
      if (primaryContracts > 0) {
        unPnlPrimary = Precision.calculatePnl(
          primarySide,
          primaryAvgPrice,
          candle.close,
          primaryContracts,
          this.params.contractValue
        );
      }

      let unPnlHedges = 0;
      for (const h of activeHedges) {
        unPnlHedges += Precision.calculatePnl(
          h.side,
          h.entryPrice,
          candle.close,
          h.quantityContracts,
          this.params.contractValue
        );
      }

      equityCurve.push({
        timestamp: candle.time,
        equity: currentEquity + unPnlPrimary + unPnlHedges,
      });
    }

    const report = PerformanceCalculator.calculateReport(this.params.initialCapitalUsd, trades, equityCurve);

    return {
      report,
      trades,
      equityCurve,
    };
  }
}

