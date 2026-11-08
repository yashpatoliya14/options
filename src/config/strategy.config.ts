/**
 * Strategy Configuration Loader
 */

import { env } from './env';
import { StrategyConfig, RiskLimits } from '../types/strategy.types';

export const defaultStrategyConfig: StrategyConfig = {
  id: 'gold-trend-grid-hedge-01',
  name: 'Gold 4H Supertrend + Grid Hedging Strategy',
  primaryInstrument: env.PRIMARY_INSTRUMENT,
  hedgeInstrument: env.HEDGE_INSTRUMENT,
  timeframe: env.TIMEFRAME,
  atrPeriod: env.SUPERTREND_ATR_PERIOD,
  atrMultiplier: env.SUPERTREND_MULTIPLIER,
  primaryTotalLots: env.PRIMARY_TOTAL_LOTS,
  primaryInitialLots: env.PRIMARY_INITIAL_LOTS,
  gridLotsPerLevel: env.GRID_LOTS_PER_LEVEL,
  gridLevels: env.GRID_LEVELS,
  gridSpacingPercent: env.GRID_SPACING_PERCENT,
  hedgeLots: env.HEDGE_LOTS,
  hedgeTriggerPercent: env.HEDGE_TRIGGER_PERCENT,
  hedgeTakeProfitPercent: env.HEDGE_TAKE_PROFIT_PERCENT,
  leverage: env.LEVERAGE,
  mode: env.TRADING_MODE,
};

export const defaultRiskLimits: RiskLimits = {
  maxPrimaryLots: env.MAX_PRIMARY_LOTS,
  maxHedgeLots: env.MAX_HEDGE_LOTS,
  maxTotalLots: env.MAX_TOTAL_LOTS,
  maxDailyLossUsd: env.MAX_DAILY_LOSS_USD,
  maxDrawdownPercent: env.MAX_DRAWDOWN_PERCENT,
  maxGridOrders: env.MAX_GRID_ORDERS,
  maxOpenHedges: env.MAX_OPEN_HEDGES,
};
