import { describe, it, expect, beforeAll } from 'vitest';
import { initializeDatabase } from '../src/database/initialize';
import { StrategyRepository } from '../src/database/repositories/strategy.repository';
import { OrderRepository } from '../src/database/repositories/order.repository';
import { HedgeRepository } from '../src/database/repositories/hedge.repository';
import { TradeRepository } from '../src/database/repositories/trade.repository';
import { nanoid } from 'nanoid';

describe('Database Repositories', () => {
  const strategyId = 'gold-trend-grid-hedge-01';
  let strategyRepo: StrategyRepository;
  let orderRepo: OrderRepository;
  let hedgeRepo: HedgeRepository;
  let tradeRepo: TradeRepository;

  beforeAll(() => {
    initializeDatabase();
    strategyRepo = new StrategyRepository();
    orderRepo = new OrderRepository();
    hedgeRepo = new HedgeRepository();
    tradeRepo = new TradeRepository();
  });

  it('saves and retrieves strategy state in SQLite', () => {
    strategyRepo.updateState(strategyId, {
      currentDirection: 'BULLISH',
      referencePrice: 2750.50,
      primaryQuantity: 125,
      primaryAveragePrice: 2750.50,
      primaryNotional: 500,
      lastProcessedCandleTimestamp: 1700000000,
    });

    const state = strategyRepo.getState(strategyId);
    expect(state).not.toBeNull();
    expect(state?.currentDirection).toBe('BULLISH');
    expect(state?.referencePrice).toBe(2750.50);
    expect(state?.primaryQuantity).toBe(125);
  });

  it('saves, updates, and retrieves orders', () => {
    const orderId = nanoid();
    orderRepo.saveOrder({
      id: orderId,
      strategyId,
      clientOrderId: `cl-${orderId}`,
      productId: 131253,
      symbol: 'XAUTUSD',
      side: 'buy',
      orderType: 'market_order',
      price: 2750,
      quantityContracts: 125,
      filledContracts: 0,
      status: 'OPEN',
      purpose: 'PRIMARY_INITIAL',
      createdAt: Date.now(),
      updatedAt: Date.now(),
    });

    const saved = orderRepo.getOrderById(orderId);
    expect(saved).not.toBeNull();
    expect(saved?.status).toBe('OPEN');

    // Update order
    orderRepo.updateOrder(orderId, {
      status: 'FILLED',
      filledContracts: 125,
      averageFillPrice: 2750,
    });

    const updated = orderRepo.getOrderById(orderId);
    expect(updated?.status).toBe('FILLED');
    expect(updated?.filledContracts).toBe(125);
  });

  it('manages hedge entries and calculates realized profit correctly', () => {
    const hedgeId = nanoid();
    hedgeRepo.saveHedgeEntry({
      id: hedgeId,
      strategyId,
      triggerLevel: 99,
      triggerPrice: 2775,
      instrument: 'PAXGUSD',
      side: 'SHORT',
      entryPrice: 2775,
      targetPrice: 2747.25,
      quantityContracts: 25,
      notionalUsd: 100,
      status: 'OPEN',
      createdAt: Date.now(),
    });

    const hedge = hedgeRepo.getHedgeByLevel(strategyId, 99);
    expect(hedge).not.toBeNull();
    expect(hedge?.id).toBe(hedgeId);
    expect(hedge?.status).toBe('OPEN');

    // Close hedge with $1.50 profit
    hedgeRepo.closeHedge(hedgeId, 1.50);

    const closed = hedgeRepo.getHedgeById(hedgeId);
    expect(closed?.status).toBe('CLOSED');
    expect(closed?.realizedPnlUsd).toBe(1.50);

    const totalProfit = hedgeRepo.getTotalRealizedHedgePnl(strategyId);
    expect(totalProfit).toBeGreaterThanOrEqual(1.50);
  });

  it('records trade and computes performance totals', () => {
    const tradeId = nanoid();
    tradeRepo.recordTrade({
      id: tradeId,
      strategyId,
      orderId: nanoid(),
      productId: 131253,
      symbol: 'XAUTUSD',
      side: 'buy',
      price: 2750,
      quantityContracts: 25,
      notionalUsd: 100,
      feesUsd: 0.05,
      realizedPnlUsd: 2.50,
      timestamp: Date.now(),
    });

    const perf = tradeRepo.getTotalPerformance(strategyId);
    expect(perf.totalTrades).toBeGreaterThanOrEqual(1);
    expect(perf.totalRealizedPnl).toBeGreaterThanOrEqual(2.50);
  });
});
