import { nanoid } from 'nanoid';
import { OrderExecutor } from '../execution/order.executor';
import { GridRepository, GridLevelRow } from '../database/repositories/grid.repository';
import { PositionManager } from './position.manager';
import { StrategyCalculations } from '../utils/calculations';
import { TrendDirection } from '../types/strategy.types';
import { InternalOrder } from '../types/order.types';
import { logger } from '../monitoring/logger';

export class GridService {
  private executor: OrderExecutor;
  private gridRepo: GridRepository;
  private positionManager: PositionManager;
  private strategyId: string;
  private productId: number;
  private symbol: string;
  private gridSpacingPercent: number;
  private gridLevelsCount: number;
  private gridLotsPerLevel: number;
  private contractValue: number;
  private tickSize: number;

  constructor(
    executor: OrderExecutor,
    gridRepo: GridRepository,
    positionManager: PositionManager,
    strategyId: string,
    productId: number = 131253, // XAUTUSD
    symbol: string = 'XAUTUSD',
    gridSpacingPercent: number = 0.5,
    gridLevelsCount: number = 5,
    gridLotsPerLevel: number = 10,
    contractValue: number = 0.001,
    tickSize: number = 0.01
  ) {
    this.executor = executor;
    this.gridRepo = gridRepo;
    this.positionManager = positionManager;
    this.strategyId = strategyId;
    this.productId = productId;
    this.symbol = symbol;
    this.gridSpacingPercent = gridSpacingPercent;
    this.gridLevelsCount = gridLevelsCount;
    this.gridLotsPerLevel = gridLotsPerLevel;
    this.contractValue = contractValue;
    this.tickSize = tickSize;
  }

  /**
   * Set up all grid levels and place limit orders
   */
  public async setupGridLevels(direction: TrendDirection, referencePrice: number): Promise<GridLevelRow[]> {
    if (direction === 'NEUTRAL' || referencePrice <= 0) {
      return [];
    }

    // Cancel any previous grid orders
    await this.cancelGridOrders();
    this.gridRepo.deleteGridLevels(this.strategyId);

    // Calculate grid levels
    const levels = StrategyCalculations.calculateGridLevels(
      direction,
      referencePrice,
      this.gridSpacingPercent,
      this.gridLevelsCount,
      this.gridLotsPerLevel,
      this.contractValue,
      this.tickSize
    );

    const side = direction === 'BULLISH' ? 'buy' : 'sell';
    const placedLevels: GridLevelRow[] = [];

    for (const lvl of levels) {
      const gridId = nanoid();

      // Place limit order
      const order = await this.executor.placeOrder({
        strategyId: this.strategyId,
        productId: this.productId,
        symbol: this.symbol,
        side,
        orderType: 'limit_order',
        price: lvl.price,
        quantityContracts: lvl.quantityContracts,
        purpose: 'GRID_ENTRY',
        contractValue: this.contractValue,
        currentMarketPrice: referencePrice,
      });

      const gridRow: GridLevelRow = {
        id: gridId,
        strategyId: this.strategyId,
        level: lvl.level,
        triggerPrice: lvl.price,
        quantity: lvl.quantityContracts,
        orderId: order.id,
        status: order.status === 'FILLED' ? 'FILLED' : 'PLACED',
        createdAt: Date.now(),
      };

      this.gridRepo.saveGridLevel(gridRow);
      placedLevels.push(gridRow);

      if (order.status === 'FILLED') {
        this.positionManager.updatePrimaryPositionOnFill(
          direction === 'BULLISH' ? 'LONG' : 'SHORT',
          lvl.quantityContracts,
          lvl.price,
          referencePrice
        );
      }
    }

    logger.info(
      {
        strategyId: this.strategyId,
        direction,
        referencePrice,
        levelsCount: placedLevels.length,
      },
      'Grid entry orders established'
    );

    return placedLevels;
  }

  /**
   * Handle fill of a grid order
   */
  public handleGridOrderFilled(order: InternalOrder, currentMarkPrice: number): void {
    if (order.purpose !== 'GRID_ENTRY' || !order.averageFillPrice) return;

    const allGrids = this.gridRepo.getGridLevels(this.strategyId);
    const matchingGrid = allGrids.find((g) => g.orderId === order.id);

    if (matchingGrid && matchingGrid.status !== 'FILLED') {
      this.gridRepo.updateGridStatus(matchingGrid.id, 'FILLED');
      const side = order.side === 'buy' ? 'LONG' : 'SHORT';

      this.positionManager.updatePrimaryPositionOnFill(
        side,
        order.filledContracts,
        order.averageFillPrice,
        currentMarkPrice
      );

      logger.info(
        {
          gridLevel: matchingGrid.level,
          price: order.averageFillPrice,
          contracts: order.filledContracts,
        },
        'Grid order filled and applied to primary position'
      );
    }
  }

  /**
   * Cancel all open grid orders
   */
  public async cancelGridOrders(): Promise<void> {
    await this.executor.cancelAllOpenGridOrders(this.strategyId);
    this.gridRepo.clearGridLevels(this.strategyId);
  }

  /**
   * Get current grid levels
   */
  public getGridLevels(): GridLevelRow[] {
    return this.gridRepo.getGridLevels(this.strategyId);
  }
}

