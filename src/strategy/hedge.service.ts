import { nanoid } from 'nanoid';
import Decimal from 'decimal.js';
import { OrderExecutor } from '../execution/order.executor';
import { HedgeRepository } from '../database/repositories/hedge.repository';
import { TradeRepository } from '../database/repositories/trade.repository';
import { StrategyRepository } from '../database/repositories/strategy.repository';
import { StrategyCalculations } from '../utils/calculations';
import { Precision } from '../utils/precision';
import { HedgePositionState, PositionSide, TrendDirection } from '../types/strategy.types';
import { InternalOrder } from '../types/order.types';
import { logger } from '../monitoring/logger';
import { alerts } from '../monitoring/alerts';

export class HedgeService {
  private executor: OrderExecutor;
  private hedgeRepo: HedgeRepository;
  private tradeRepo: TradeRepository;
  private strategyRepo: StrategyRepository;
  private strategyId: string;
  private hedgeProductId: number;
  private hedgeSymbol: string;
  private hedgeTriggerPercent: number;
  private hedgeTakeProfitPercent: number;
  private hedgeLots: number;
  private maxOpenHedges: number;
  private contractValue: number;
  private tickSize: number;

  constructor(
    executor: OrderExecutor,
    hedgeRepo: HedgeRepository,
    tradeRepo: TradeRepository,
    strategyRepo: StrategyRepository,
    strategyId: string,
    hedgeProductId: number = 123006, // PAXGUSD
    hedgeSymbol: string = 'PAXGUSD',
    hedgeTriggerPercent: number = 1.0,
    hedgeTakeProfitPercent: number = 1.0,
    hedgeLots: number = 10,
    maxOpenHedges: number = 10,
    contractValue: number = 0.001,
    tickSize: number = 0.01
  ) {
    this.executor = executor;
    this.hedgeRepo = hedgeRepo;
    this.tradeRepo = tradeRepo;
    this.strategyRepo = strategyRepo;
    this.strategyId = strategyId;
    this.hedgeProductId = hedgeProductId;
    this.hedgeSymbol = hedgeSymbol;
    this.hedgeTriggerPercent = hedgeTriggerPercent;
    this.hedgeTakeProfitPercent = hedgeTakeProfitPercent;
    this.hedgeLots = hedgeLots;
    this.maxOpenHedges = maxOpenHedges;
    this.contractValue = contractValue;
    this.tickSize = tickSize;
  }

  /**
   * Evaluate if current primary price triggers any new hedge levels
   */
  public async evaluateHedgeTriggers(
    primaryDirection: TrendDirection,
    referencePrice: number,
    currentPrimaryPrice: number,
    currentHedgePrice: number
  ): Promise<HedgePositionState | null> {
    if (primaryDirection === 'NEUTRAL' || referencePrice <= 0 || currentPrimaryPrice <= 0 || currentHedgePrice <= 0) {
      return null;
    }

    const ref = new Decimal(referencePrice);
    const curr = new Decimal(currentPrimaryPrice);
    const triggerPctDec = new Decimal(this.hedgeTriggerPercent).dividedBy(100);

    // Calculate how many 1% favorable moves have occurred
    let favorableMovePercent: Decimal;
    if (primaryDirection === 'BULLISH') {
      favorableMovePercent = curr.minus(ref).dividedBy(ref);
    } else {
      favorableMovePercent = ref.minus(curr).dividedBy(ref);
    }

    if (favorableMovePercent.isNegative()) {
      return null;
    }

    // Number of 1% steps reached: e.g. 1.05% -> level 1; 2.1% -> level 2
    const targetLevel = favorableMovePercent.dividedBy(triggerPctDec).floor().toNumber();
    if (targetLevel < 1) {
      return null;
    }

    // Check open hedges count against max limit
    const openHedges = this.hedgeRepo.getOpenHedges(this.strategyId);
    if (openHedges.length >= this.maxOpenHedges) {
      logger.warn({ openHedges: openHedges.length, max: this.maxOpenHedges }, 'Max open hedges reached');
      return null;
    }

    // Check if hedge for this level is already active
    const existing = this.hedgeRepo.getHedgeByLevel(this.strategyId, targetLevel);
    if (existing) {
      return null;
    }

    // Place new hedge entry
    return this.openHedge(targetLevel, primaryDirection, currentPrimaryPrice, currentHedgePrice);
  }

  /**
   * Open an opposite-direction hedge position
   */
  private async openHedge(
    level: number,
    primaryDirection: TrendDirection,
    currentPrimaryPrice: number,
    currentHedgePrice: number
  ): Promise<HedgePositionState | null> {
    const hedgeSide: PositionSide = StrategyCalculations.getHedgeSide(primaryDirection);
    const orderSide: 'buy' | 'sell' = hedgeSide === 'LONG' ? 'buy' : 'sell';

    const quantityContracts = this.hedgeLots;

    const hedgeId = nanoid();
    const entryOrder = await this.executor.placeOrder({
      strategyId: this.strategyId,
      productId: this.hedgeProductId,
      symbol: this.hedgeSymbol,
      side: orderSide,
      orderType: 'market_order',
      quantityContracts,
      purpose: 'HEDGE_ENTRY',
      contractValue: this.contractValue,
      currentMarketPrice: currentHedgePrice,
    });

    const fillPrice = entryOrder.averageFillPrice || currentHedgePrice;
    const targetPrice = StrategyCalculations.calculateHedgeTakeProfitPrice(
      hedgeSide,
      fillPrice,
      this.hedgeTakeProfitPercent,
      this.tickSize
    );

    // Place Take-Profit Limit Order
    const tpOrderSide: 'buy' | 'sell' = hedgeSide === 'LONG' ? 'sell' : 'buy';
    const tpOrder = await this.executor.placeOrder({
      strategyId: this.strategyId,
      productId: this.hedgeProductId,
      symbol: this.hedgeSymbol,
      side: tpOrderSide,
      orderType: 'limit_order',
      price: targetPrice,
      quantityContracts,
      purpose: 'HEDGE_TP',
      contractValue: this.contractValue,
      currentMarketPrice: currentHedgePrice,
    });

    const hedgeState: HedgePositionState = {
      id: hedgeId,
      strategyId: this.strategyId,
      triggerLevel: level,
      triggerPrice: currentPrimaryPrice,
      instrument: this.hedgeSymbol,
      side: hedgeSide,
      entryPrice: fillPrice,
      targetPrice,
      quantityContracts,
      notionalUsd: Precision.calculateNotional(quantityContracts, fillPrice, this.contractValue),
      status: 'OPEN',
      orderId: entryOrder.id,
      takeProfitOrderId: tpOrder.id,
      createdAt: Date.now(),
    };

    this.hedgeRepo.saveHedgeEntry(hedgeState);
    this.strategyRepo.updateState(this.strategyId, { lastHedgeTriggerLevel: level });

    alerts.info(
      'Hedge Opened',
      `Level ${level}: ${hedgeSide} ${quantityContracts} contracts on ${this.hedgeSymbol} @ ${fillPrice}, Target: ${targetPrice}`
    );

    return hedgeState;
  }

  /**
   * Monitor open hedges and book profit when 1% target price is reached
   */
  public async checkHedgeTakeProfits(currentHedgePrice: number): Promise<HedgePositionState[]> {
    if (currentHedgePrice <= 0) return [];

    const openHedges = this.hedgeRepo.getOpenHedges(this.strategyId);
    const closedHedges: HedgePositionState[] = [];

    for (const hedge of openHedges) {
      let isTakeProfitReached = false;

      if (hedge.side === 'SHORT' && currentHedgePrice <= hedge.targetPrice) {
        // Short hedge bought back lower -> profit
        isTakeProfitReached = true;
      } else if (hedge.side === 'LONG' && currentHedgePrice >= hedge.targetPrice) {
        // Long hedge sold higher -> profit
        isTakeProfitReached = true;
      }

      if (isTakeProfitReached) {
        await this.closeHedgeTakeProfit(hedge, hedge.targetPrice);
        closedHedges.push(hedge);
      }
    }

    return closedHedges;
  }

  /**
   * Close a specific hedge that achieved its take-profit target
   */
  private async closeHedgeTakeProfit(hedge: HedgePositionState, exitPrice: number): Promise<void> {
    const realizedPnl = Precision.calculatePnl(
      hedge.side,
      hedge.entryPrice,
      exitPrice,
      hedge.quantityContracts,
      this.contractValue
    );

    const notional = Precision.calculateNotional(hedge.quantityContracts, exitPrice, this.contractValue);
    const fees = new Decimal(notional).times(0.0002).toNumber(); // Maker fee on TP limit fill

    // Cancel existing TP order if it was tracked separately
    if (hedge.takeProfitOrderId) {
      await this.executor.cancelOrder(hedge.takeProfitOrderId);
    }

    // Record closing trade
    this.tradeRepo.recordTrade({
      id: nanoid(),
      strategyId: this.strategyId,
      orderId: hedge.takeProfitOrderId || nanoid(),
      productId: this.hedgeProductId,
      symbol: this.hedgeSymbol,
      side: hedge.side === 'LONG' ? 'sell' : 'buy',
      price: exitPrice,
      quantityContracts: hedge.quantityContracts,
      notionalUsd: notional,
      feesUsd: fees,
      realizedPnlUsd: realizedPnl,
      timestamp: Date.now(),
    });

    // Mark hedge closed
    this.hedgeRepo.closeHedge(hedge.id, realizedPnl);

    logger.info(
      {
        hedgeId: hedge.id,
        level: hedge.triggerLevel,
        entryPrice: hedge.entryPrice,
        exitPrice,
        realizedPnl,
        contracts: hedge.quantityContracts,
      },
      'Hedge Take-Profit achieved and booked. Primary position remains open.'
    );

    alerts.info(
      'Hedge Profit Booked',
      `Hedge Level ${hedge.triggerLevel} hit target ${exitPrice}! Realized P&L: +$${realizedPnl.toFixed(2)}. Primary position preserved.`
    );
  }

  /**
   * Close all active hedges (e.g. on trend reversal)
   */
  public async closeAllHedgesOnReversal(currentHedgePrice: number): Promise<void> {
    const openHedges = this.hedgeRepo.getOpenHedges(this.strategyId);

    for (const hedge of openHedges) {
      const realizedPnl = Precision.calculatePnl(
        hedge.side,
        hedge.entryPrice,
        currentHedgePrice,
        hedge.quantityContracts,
        this.contractValue
      );

      if (hedge.takeProfitOrderId) {
        await this.executor.cancelOrder(hedge.takeProfitOrderId);
      }

      this.hedgeRepo.closeHedge(hedge.id, realizedPnl);

      const notional = Precision.calculateNotional(hedge.quantityContracts, currentHedgePrice, this.contractValue);
      this.tradeRepo.recordTrade({
        id: nanoid(),
        strategyId: this.strategyId,
        orderId: nanoid(),
        productId: this.hedgeProductId,
        symbol: this.hedgeSymbol,
        side: hedge.side === 'LONG' ? 'sell' : 'buy',
        price: currentHedgePrice,
        quantityContracts: hedge.quantityContracts,
        notionalUsd: notional,
        feesUsd: notional * 0.0005,
        realizedPnlUsd: realizedPnl,
        timestamp: Date.now(),
      });
    }

    logger.info({ closedCount: openHedges.length }, 'Closed all open hedges on reversal');
  }

  /**
   * Get all currently open hedge positions
   */
  public getOpenHedges(): HedgePositionState[] {
    return this.hedgeRepo.getOpenHedges(this.strategyId);
  }
}

