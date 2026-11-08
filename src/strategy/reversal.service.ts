import { PositionManager } from './position.manager';
import { GridService } from './grid.service';
import { HedgeService } from './hedge.service';
import { OrderExecutor } from '../execution/order.executor';
import { StrategyRepository } from '../database/repositories/strategy.repository';
import { TrendDirection, PositionSide } from '../types/strategy.types';
import { logger } from '../monitoring/logger';
import { alerts } from '../monitoring/alerts';
import { telegram } from '../monitoring/telegram';

export class ReversalService {
  private positionManager: PositionManager;
  private gridService: GridService;
  private hedgeService: HedgeService;
  private executor: OrderExecutor;
  private strategyRepo: StrategyRepository;
  private strategyId: string;
  private primaryProductId: number;
  private primarySymbol: string;
  private hedgeProductId: number;
  private hedgeSymbol: string;

  constructor(
    positionManager: PositionManager,
    gridService: GridService,
    hedgeService: HedgeService,
    executor: OrderExecutor,
    strategyRepo: StrategyRepository,
    strategyId: string,
    primaryProductId: number = 131253,
    primarySymbol: string = 'XAUTUSD',
    hedgeProductId: number = 123006,
    hedgeSymbol: string = 'PAXGUSD'
  ) {
    this.positionManager = positionManager;
    this.gridService = gridService;
    this.hedgeService = hedgeService;
    this.executor = executor;
    this.strategyRepo = strategyRepo;
    this.strategyId = strategyId;
    this.primaryProductId = primaryProductId;
    this.primarySymbol = primarySymbol;
    this.hedgeProductId = hedgeProductId;
    this.hedgeSymbol = hedgeSymbol;
  }

  /**
   * Execute full reversal sequence:
   * 1. Stop & cancel pending grid orders
   * 2. Close active hedge positions
   * 3. Close existing primary position
   * 4. Reset strategy state
   */
  public async handleTrendReversal(
    newDirection: TrendDirection,
    previousDirection: TrendDirection,
    currentPrimaryPrice: number,
    currentHedgePrice: number
  ): Promise<void> {
    logger.info(
      {
        strategyId: this.strategyId,
        from: previousDirection,
        to: newDirection,
        primaryPrice: currentPrimaryPrice,
      },
      'Initiating trend reversal handling...'
    );

    alerts.info(
      'Trend Reversal Detected',
      `Supertrend reversed from ${previousDirection} to ${newDirection}. Rebalancing portfolio.`
    );

    // 1. Cancel pending grid limit orders
    await this.gridService.cancelGridOrders();

    // 2. Close all active hedges and realize P&L
    await this.hedgeService.closeAllHedgesOnReversal(currentHedgePrice);

    // 3. Close the existing primary position
    const summary = this.positionManager.getPortfolioSummary(currentPrimaryPrice, currentHedgePrice);
    if (summary.primaryPosition && summary.primaryPosition.quantityContracts > 0) {
      const closeSide: 'buy' | 'sell' = summary.primaryPosition.side === 'LONG' ? 'sell' : 'buy';
      logger.info(
        {
          contracts: summary.primaryPosition.quantityContracts,
          side: closeSide,
          price: currentPrimaryPrice,
        },
        'Closing primary position on reversal'
      );

      await this.executor.placeOrder({
        strategyId: this.strategyId,
        productId: this.primaryProductId,
        symbol: this.primarySymbol,
        side: closeSide,
        orderType: 'market_order',
        quantityContracts: summary.primaryPosition.quantityContracts,
        purpose: 'REVERSAL_CLOSE',
        currentMarketPrice: currentPrimaryPrice,
      });

      this.positionManager.resetPrimaryPosition();
    }

    // 4. Update strategy state in SQLite
    this.strategyRepo.updateState(this.strategyId, {
      currentDirection: newDirection,
      referencePrice: currentPrimaryPrice,
      primaryQuantity: 0,
      primaryAveragePrice: 0,
      primaryNotional: 0,
      currentGridLevel: 0,
      lastHedgeTriggerLevel: 0,
    });

    logger.info({ newDirection, referencePrice: currentPrimaryPrice }, 'Reversal sequence completed.');

    telegram.notifyTrendReversal({
      fromDirection: previousDirection,
      toDirection: newDirection,
      primaryLotsClosed: summary.primaryPosition?.quantityContracts || 0,
      hedgesClosedCount: summary.openHedgeCount || 0,
    }).catch(() => {});
  }
}

