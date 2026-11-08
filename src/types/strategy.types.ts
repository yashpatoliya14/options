/**
 * Strategy Domain Types for Gold Trend + Grid Hedging Strategy
 */

export type TrendDirection = 'BULLISH' | 'BEARISH' | 'NEUTRAL';
export type PositionSide = 'LONG' | 'SHORT';
export type StrategyStatus = 'RUNNING' | 'PAUSED' | 'STOPPED' | 'KILLED';
export type TradingMode = 'BACKTEST' | 'PAPER' | 'LIVE';

export interface StrategyConfig {
  id: string;
  name: string;
  primaryInstrument: string; // e.g. "XAUTUSD"
  hedgeInstrument: string;   // e.g. "PAXGUSD"
  timeframe: string;         // e.g. "4h"
  atrPeriod: number;         // default: 10
  atrMultiplier: number;     // default: 3.0
  primaryTotalLots: number;   // default: 100 lots
  primaryInitialLots: number; // default: 50 lots
  gridLotsPerLevel: number;   // default: 10 lots
  gridLevels: number;         // default: 5
  gridSpacingPercent: number; // default: 0.5%
  hedgeLots: number;          // default: 10 lots
  hedgeTriggerPercent: number;    // default: 1.0%
  hedgeTakeProfitPercent: number; // default: 1.0%
  leverage: number;               // default: 10
  mode: TradingMode;
}

export interface RiskLimits {
  maxPrimaryLots: number;
  maxHedgeLots: number;
  maxTotalLots: number;
  maxDailyLossUsd: number;
  maxDrawdownPercent: number;
  maxGridOrders: number;
  maxOpenHedges: number;
}

export interface StrategyState {
  strategyId: string;
  currentDirection: TrendDirection;
  lastProcessedCandleTimestamp: number;
  referencePrice: number;
  primaryAveragePrice: number;
  primaryQuantity: number; // in contracts
  primaryNotional: number;
  currentGridLevel: number;
  lastHedgeTriggerLevel: number;
  updatedAt: number;
}

export interface SupertrendResult {
  trend: TrendDirection;
  supertrend: number;
  upperBand: number;
  lowerBand: number;
  atr: number;
  isReversal: boolean;
  candleTimestamp: number;
}

export interface GridLevelDefinition {
  level: number;
  price: number;
  quantityContracts: number;
  notionalUsd: number;
  orderId?: string;
  status: 'PENDING' | 'PLACED' | 'FILLED' | 'CANCELLED';
}

export interface HedgePositionState {
  id: string;
  strategyId: string;
  triggerLevel: number; // e.g. 1 (+1%), 2 (+2%), 3 (+3%)
  triggerPrice: number; // Primary instrument price when triggered
  instrument: string;   // Secondary instrument (e.g. PAXGUSD)
  side: PositionSide;   // Opposite of primary position
  entryPrice: number;
  targetPrice: number;  // 1% profit target
  quantityContracts: number;
  notionalUsd: number;
  status: 'PENDING' | 'OPEN' | 'TAKE_PROFIT_PLACED' | 'CLOSED' | 'CANCELLED';
  orderId?: string;
  takeProfitOrderId?: string;
  realizedPnlUsd?: number;
  feesUsd?: number;
  createdAt: number;
  closedAt?: number;
}

export interface StrategyStatusReport {
  status: StrategyStatus;
  mode: TradingMode;
  direction: TrendDirection;
  primarySymbol: string;
  primaryPrice: number;
  primaryPositionContracts: number;
  primaryNotionalUsd: number;
  primaryAvgPrice: number;
  primaryUnrealizedPnlUsd: number;
  hedgeSymbol: string;
  hedgePrice: number;
  openHedgesCount: number;
  totalHedgeNotionalUsd: number;
  totalRealizedHedgePnlUsd: number;
  openGridOrdersCount: number;
  dailyRealizedPnlUsd: number;
  dailyFeesUsd: number;
  marginUtilizationPct: number;
  uptimeSeconds: number;
}

