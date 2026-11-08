import Decimal from 'decimal.js';
import { PositionRepository } from '../database/repositories/position.repository';
import { HedgeRepository } from '../database/repositories/hedge.repository';
import { StrategyRepository } from '../database/repositories/strategy.repository';
import { InternalPosition } from '../types/order.types';
import { PositionSide, TrendDirection } from '../types/strategy.types';
import { Precision } from '../utils/precision';
import { logger } from '../monitoring/logger';

export interface PortfolioSummary {
  primaryPosition: InternalPosition | null;
  primaryNotionalUsd: number;
  primaryUnrealizedPnl: number;
  openHedgeCount: number;
  totalHedgeContracts: number;
  hedgeNotionalUsd: number;
  hedgeUnrealizedPnl: number;
  realizedHedgeProfitTotalUsd: number;
  totalExposureUsd: number; // primary + hedge
  netExposureUsd: number;   // primary - hedge
  marginUtilizedUsd: number;
  combinedUnrealizedPnl: number;
  totalRealizedPnl: number;
}

export class PositionManager {
  private positionRepo: PositionRepository;
  private hedgeRepo: HedgeRepository;
  private strategyRepo: StrategyRepository;
  private strategyId: string;
  private primarySymbol: string;
  private primaryProductId: number;
  private hedgeSymbol: string;
  private hedgeProductId: number;
  private contractValue: number;
  private leverage: number;

  constructor(
    positionRepo: PositionRepository,
    hedgeRepo: HedgeRepository,
    strategyRepo: StrategyRepository,
    strategyId: string,
    primarySymbol: string = 'XAUTUSD',
    primaryProductId: number = 131253,
    hedgeSymbol: string = 'PAXGUSD',
    hedgeProductId: number = 123006,
    contractValue: number = 0.001,
    leverage: number = 10
  ) {
    this.positionRepo = positionRepo;
    this.hedgeRepo = hedgeRepo;
    this.strategyRepo = strategyRepo;
    this.strategyId = strategyId;
    this.primarySymbol = primarySymbol;
    this.primaryProductId = primaryProductId;
    this.hedgeSymbol = hedgeSymbol;
    this.hedgeProductId = hedgeProductId;
    this.contractValue = contractValue;
    this.leverage = leverage;
  }

  /**
   * Record or update primary position on fill
   */
  public updatePrimaryPositionOnFill(
    side: PositionSide,
    newFillContracts: number,
    fillPrice: number,
    currentMarkPrice: number
  ): InternalPosition {
    const existing = this.positionRepo.getPosition(this.strategyId, this.primaryProductId);

    let updatedContracts: number;
    let newAvgPrice: number;

    if (!existing || existing.quantityContracts === 0) {
      updatedContracts = newFillContracts;
      newAvgPrice = fillPrice;
    } else if (existing.side === side) {
      // Adding to existing position (e.g. grid fill)
      const existingNotional = new Decimal(existing.quantityContracts).times(existing.averageEntryPrice);
      const addedNotional = new Decimal(newFillContracts).times(fillPrice);
      const totalContracts = existing.quantityContracts + newFillContracts;

      newAvgPrice = existingNotional.plus(addedNotional).dividedBy(totalContracts).toNumber();
      updatedContracts = totalContracts;
    } else {
      // Reducing or closing position
      if (newFillContracts >= existing.quantityContracts) {
        updatedContracts = 0;
        newAvgPrice = 0;
      } else {
        updatedContracts = existing.quantityContracts - newFillContracts;
        newAvgPrice = existing.averageEntryPrice;
      }
    }

    const unrealizedPnl = updatedContracts > 0
      ? Precision.calculatePnl(side, newAvgPrice, currentMarkPrice, updatedContracts, this.contractValue)
      : 0;

    const notional = Precision.calculateNotional(updatedContracts, currentMarkPrice, this.contractValue);

    const positionRecord: InternalPosition = {
      strategyId: this.strategyId,
      productId: this.primaryProductId,
      symbol: this.primarySymbol,
      side,
      quantityContracts: updatedContracts,
      averageEntryPrice: newAvgPrice,
      currentMarkPrice,
      unrealizedPnlUsd: unrealizedPnl,
      realizedPnlUsd: existing?.realizedPnlUsd || 0,
      leverage: this.leverage,
      updatedAt: Date.now(),
    };

    this.positionRepo.savePosition(positionRecord);

    // Also update strategy state in DB
    this.strategyRepo.updateState(this.strategyId, {
      primaryAveragePrice: newAvgPrice,
      primaryQuantity: updatedContracts,
      primaryNotional: notional,
    });

    logger.info(
      {
        symbol: this.primarySymbol,
        side,
        contracts: updatedContracts,
        avgPrice: newAvgPrice,
        notional,
      },
      'Updated Primary Position'
    );

    return positionRecord;
  }

  /**
   * Close or reset primary position (e.g. on trend reversal)
   */
  public resetPrimaryPosition(): void {
    const existing = this.positionRepo.getPosition(this.strategyId, this.primaryProductId);
    if (existing) {
      this.positionRepo.savePosition({
        ...existing,
        quantityContracts: 0,
        averageEntryPrice: 0,
        unrealizedPnlUsd: 0,
        updatedAt: Date.now(),
      });
    }

    this.strategyRepo.updateState(this.strategyId, {
      primaryAveragePrice: 0,
      primaryQuantity: 0,
      primaryNotional: 0,
      referencePrice: 0,
      currentGridLevel: 0,
      lastHedgeTriggerLevel: 0,
    });
  }

  /**
   * Update mark prices and unrealized PnL for active positions
   */
  public updateMarkPrices(primaryMarkPrice: number, hedgeMarkPrice: number): void {
    const primaryPos = this.positionRepo.getPosition(this.strategyId, this.primaryProductId);
    if (primaryPos && primaryPos.quantityContracts > 0) {
      const uPnl = Precision.calculatePnl(
        primaryPos.side,
        primaryPos.averageEntryPrice,
        primaryMarkPrice,
        primaryPos.quantityContracts,
        this.contractValue
      );
      this.positionRepo.updatePnL(this.strategyId, this.primaryProductId, primaryMarkPrice, uPnl);
    }
  }

  /**
   * Get full portfolio exposure & summary
   */
  public getPortfolioSummary(primaryMarkPrice: number, hedgeMarkPrice: number): PortfolioSummary {
    const primaryPos = this.positionRepo.getPosition(this.strategyId, this.primaryProductId);
    const openHedges = this.hedgeRepo.getOpenHedges(this.strategyId);
    const realizedHedgeProfit = this.hedgeRepo.getTotalRealizedPnl(this.strategyId);

    const primaryContracts = primaryPos?.quantityContracts || 0;
    const primaryAvgPrice = primaryPos?.averageEntryPrice || 0;
    const primarySide = primaryPos?.side || 'LONG';

    const primaryNotional = Precision.calculateNotional(primaryContracts, primaryMarkPrice, this.contractValue);
    const primaryUnrealizedPnl = primaryContracts > 0
      ? Precision.calculatePnl(primarySide, primaryAvgPrice, primaryMarkPrice, primaryContracts, this.contractValue)
      : 0;

    let totalHedgeContracts = 0;
    let hedgeUnrealizedPnl = 0;

    for (const h of openHedges) {
      totalHedgeContracts += h.quantityContracts;
      const hPnl = Precision.calculatePnl(
        h.side,
        h.entryPrice,
        hedgeMarkPrice,
        h.quantityContracts,
        this.contractValue
      );
      hedgeUnrealizedPnl += hPnl;
    }

    const hedgeNotional = Precision.calculateNotional(totalHedgeContracts, hedgeMarkPrice, this.contractValue);
    const totalExposure = primaryNotional + hedgeNotional;
    const netExposure = Math.abs(primaryNotional - hedgeNotional);
    const marginUtilized = (primaryNotional / this.leverage) + (hedgeNotional / this.leverage);
    const combinedUnrealizedPnl = primaryUnrealizedPnl + hedgeUnrealizedPnl;
    const totalRealizedPnl = (primaryPos?.realizedPnlUsd || 0) + realizedHedgeProfit;

    return {
      primaryPosition: primaryPos || null,
      primaryNotionalUsd: Precision.d(primaryNotional).toDecimalPlaces(2).toNumber(),
      primaryUnrealizedPnl: Precision.d(primaryUnrealizedPnl).toDecimalPlaces(2).toNumber(),
      openHedgeCount: openHedges.length,
      totalHedgeContracts,
      hedgeNotionalUsd: Precision.d(hedgeNotional).toDecimalPlaces(2).toNumber(),
      hedgeUnrealizedPnl: Precision.d(hedgeUnrealizedPnl).toDecimalPlaces(2).toNumber(),
      realizedHedgeProfitTotalUsd: Precision.d(realizedHedgeProfit).toDecimalPlaces(2).toNumber(),
      totalExposureUsd: Precision.d(totalExposure).toDecimalPlaces(2).toNumber(),
      netExposureUsd: Precision.d(netExposure).toDecimalPlaces(2).toNumber(),
      marginUtilizedUsd: Precision.d(marginUtilized).toDecimalPlaces(2).toNumber(),
      combinedUnrealizedPnl: Precision.d(combinedUnrealizedPnl).toDecimalPlaces(2).toNumber(),
      totalRealizedPnl: Precision.d(totalRealizedPnl).toDecimalPlaces(2).toNumber(),
    };
  }
}

