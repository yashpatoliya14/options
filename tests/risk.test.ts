import { describe, it, expect, beforeAll } from 'vitest';
import { RiskManager } from '../src/strategy/risk.manager';
import { RiskLimits } from '../src/types/strategy.types';
import { TradeRepository } from '../src/database/repositories/trade.repository';
import { RiskRepository } from '../src/database/repositories/risk.repository';
import { initializeDatabase } from '../src/database/initialize';

describe('Risk Manager', () => {
  const limits: RiskLimits = {
    maxPrimaryLots: 200,
    maxHedgeLots: 100,
    maxTotalLots: 300,
    maxDailyLossUsd: 150,
    maxDrawdownPercent: 15,
    maxGridOrders: 5,
    maxOpenHedges: 10,
  };

  let riskManager: RiskManager;
  let tradeRepo: TradeRepository;
  let riskRepo: RiskRepository;
  const strategyId = 'gold-trend-grid-hedge-01'; // Seeded strategy id with valid foreign key

  beforeAll(() => {
    initializeDatabase();
    tradeRepo = new TradeRepository();
    riskRepo = new RiskRepository();
    riskManager = new RiskManager(limits, tradeRepo, riskRepo, strategyId);
  });

  it('approves pre-trade within risk limits', () => {
    const res = riskManager.validatePreTrade('PRIMARY', 50, 0, 0);
    expect(res.allowed).toBe(true);
  });

  it('rejects order if primary lots exceeds max limit (200 lots)', () => {
    // Current primary 180 lots, adding 50 = 230 > 200
    const res = riskManager.validatePreTrade('PRIMARY', 50, 180, 0);
    expect(res.allowed).toBe(false);
    expect(res.breachedLimit).toBe('MAX_PRIMARY_LOTS');
  });

  it('rejects order if hedge lots exceeds max limit (100 lots)', () => {
    // Current hedge 95 lots, adding 10 = 105 > 100
    const res = riskManager.validatePreTrade('HEDGE', 10, 100, 95);
    expect(res.allowed).toBe(false);
    expect(res.breachedLimit).toBe('MAX_HEDGE_LOTS');
  });

  it('rejects order if total combined lots exceeds max limit (250 cap)', () => {
    const customRiskManager = new RiskManager(
      { ...limits, maxTotalLots: 250 },
      tradeRepo,
      riskRepo,
      strategyId
    );

    // Primary: 160 <= 200. Hedge: 80 + 20 = 100 <= 100.
    // Total: 160 + 80 + 20 = 260 > 250.
    const res = customRiskManager.validatePreTrade('HEDGE', 20, 160, 80);
    expect(res.allowed).toBe(false);
    expect(res.breachedLimit).toBe('MAX_TOTAL_LOTS');
  });

  it('triggers emergency stop on max daily loss breach ($150)', () => {
    // Isolate daily loss by setting drawdown limit higher
    const customRiskManager = new RiskManager(
      { ...limits, maxDrawdownPercent: 50 },
      tradeRepo,
      riskRepo,
      strategyId
    );

    const fakeSummary: any = {
      primaryPosition: null,
      primaryNotionalUsd: 1000,
      primaryUnrealizedPnl: -250,
      openHedgeCount: 0,
      totalHedgeContracts: 0,
      hedgeNotionalUsd: 0,
      hedgeUnrealizedPnl: 0,
      realizedHedgeProfitTotalUsd: 0,
      totalExposureUsd: 1000,
      netExposureUsd: 1000,
      marginUtilizedUsd: 100,
      combinedUnrealizedPnl: -250, // Loss of $250 > $150 limit
      totalRealizedPnl: 0,
    };

    const res = customRiskManager.evaluatePortfolioRisk(fakeSummary, 1000);
    expect(res.allowed).toBe(false);
    expect(res.breachedLimit).toBe('MAX_DAILY_LOSS');
    expect(customRiskManager.isEmergencyStopped()).toBe(true);

    // Pre-trade must now be rejected
    const postStopCheck = customRiskManager.validatePreTrade('PRIMARY', 10, 0, 0);
    expect(postStopCheck.allowed).toBe(false);
    expect(postStopCheck.breachedLimit).toBe('EMERGENCY_STOP');
  });

  it('triggers emergency stop on max drawdown breach (> 15%)', () => {
    const customRiskManager = new RiskManager(
      { ...limits, maxDailyLossUsd: 1000 },
      tradeRepo,
      riskRepo,
      strategyId
    );

    const fakeSummary: any = {
      primaryPosition: null,
      primaryNotionalUsd: 1000,
      primaryUnrealizedPnl: -200,
      openHedgeCount: 0,
      totalHedgeContracts: 0,
      hedgeNotionalUsd: 0,
      hedgeUnrealizedPnl: 0,
      realizedHedgeProfitTotalUsd: 0,
      totalExposureUsd: 1000,
      netExposureUsd: 1000,
      marginUtilizedUsd: 100,
      combinedUnrealizedPnl: -200, // 20% drawdown on $1000 equity > 15%
      totalRealizedPnl: 0,
    };

    const res = customRiskManager.evaluatePortfolioRisk(fakeSummary, 1000);
    expect(res.allowed).toBe(false);
    expect(res.breachedLimit).toBe('MAX_DRAWDOWN');
    expect(customRiskManager.isEmergencyStopped()).toBe(true);
  });
});
