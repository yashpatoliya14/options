import { nanoid } from 'nanoid';
import Decimal from 'decimal.js';
import { DeltaClient } from '../exchange/delta.client';
import { OrderRepository } from '../database/repositories/order.repository';
import { TradeRepository } from '../database/repositories/trade.repository';
import { InternalOrder, InternalOrderStatus, OrderPurpose } from '../types/order.types';
import { TradingMode } from '../types/strategy.types';
import { Precision } from '../utils/precision';
import { Idempotency } from '../utils/idempotency';
import { logger } from '../monitoring/logger';
import { alerts } from '../monitoring/alerts';

export interface PlaceOrderParams {
  strategyId: string;
  productId: number;
  symbol: string;
  side: 'buy' | 'sell';
  orderType: 'limit_order' | 'market_order';
  quantityContracts: number;
  price?: number;
  purpose: OrderPurpose;
  contractValue?: number;
  currentMarketPrice?: number;
}

export class OrderExecutor {
  private deltaClient: DeltaClient;
  private orderRepo: OrderRepository;
  private tradeRepo: TradeRepository;
  private mode: TradingMode;
  private takerFeeRate = 0.0005; // 0.05%
  private makerFeeRate = 0.0002; // 0.02%

  constructor(
    deltaClient: DeltaClient,
    orderRepo: OrderRepository,
    tradeRepo: TradeRepository,
    mode: TradingMode = 'PAPER'
  ) {
    this.deltaClient = deltaClient;
    this.orderRepo = orderRepo;
    this.tradeRepo = tradeRepo;
    this.mode = mode;
  }

  public setMode(mode: TradingMode): void {
    this.mode = mode;
  }

  public getMode(): TradingMode {
    return this.mode;
  }

  /**
   * Place an order (handles PAPER and LIVE modes)
   */
  public async placeOrder(params: PlaceOrderParams): Promise<InternalOrder> {
    const orderId = nanoid();
    const clientOrderId = Idempotency.generateClientOrderId(params.strategyId, params.purpose, params.side);
    const contractValue = params.contractValue ?? 0.001;

    const orderRecord: InternalOrder = {
      id: orderId,
      strategyId: params.strategyId,
      clientOrderId,
      productId: params.productId,
      symbol: params.symbol,
      side: params.side,
      orderType: params.orderType,
      price: params.price,
      quantityContracts: params.quantityContracts,
      filledContracts: 0,
      status: 'PENDING',
      purpose: params.purpose,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    this.orderRepo.createOrder(orderRecord);

    if (this.mode === 'PAPER' || this.mode === 'BACKTEST') {
      return this.executePaperOrder(orderRecord, params.currentMarketPrice, contractValue);
    } else {
      return this.executeLiveOrder(orderRecord, params);
    }
  }

  /**
   * Execute order in PAPER mode
   */
  private executePaperOrder(
    order: InternalOrder,
    currentMarketPrice?: number,
    contractValue: number = 0.001
  ): InternalOrder {
    const now = Date.now();

    if (order.orderType === 'market_order') {
      const fillPrice = currentMarketPrice || order.price || 0;
      const notional = Precision.calculateNotional(order.quantityContracts, fillPrice, contractValue);
      const fee = new Decimal(notional).times(this.takerFeeRate).toNumber();

      order.status = 'FILLED';
      order.filledContracts = order.quantityContracts;
      order.averageFillPrice = fillPrice;
      order.updatedAt = now;

      this.orderRepo.updateOrder(order.id, {
        status: 'FILLED',
        filledContracts: order.quantityContracts,
        averageFillPrice: fillPrice,
      });

      // Record paper trade
      this.tradeRepo.recordTrade({
        id: nanoid(),
        strategyId: order.strategyId,
        orderId: order.id,
        productId: order.productId,
        symbol: order.symbol,
        side: order.side,
        price: fillPrice,
        quantityContracts: order.quantityContracts,
        notionalUsd: notional,
        feesUsd: fee,
        timestamp: now,
      });

      logger.info(
        {
          orderId: order.id,
          symbol: order.symbol,
          side: order.side,
          qty: order.quantityContracts,
          fillPrice,
          notional,
          purpose: order.purpose,
        },
        'Paper Market Order FILLED'
      );

      return order;
    } else {
      // Limit order: opens as OPEN, waits for price tick to trigger
      order.status = 'OPEN';
      order.updatedAt = now;

      this.orderRepo.updateOrder(order.id, {
        status: 'OPEN',
      });

      logger.info(
        {
          orderId: order.id,
          symbol: order.symbol,
          side: order.side,
          qty: order.quantityContracts,
          limitPrice: order.price,
          purpose: order.purpose,
        },
        'Paper Limit Order PLACED (OPEN)'
      );

      return order;
    }
  }

  /**
   * Execute order in LIVE mode via Delta Exchange India REST API
   */
  private async executeLiveOrder(order: InternalOrder, params: PlaceOrderParams): Promise<InternalOrder> {
    try {
      this.orderRepo.updateOrder(order.id, { status: 'SUBMITTED' });

      const apiResponse = await this.deltaClient.placeOrder({
        product_id: params.productId,
        size: params.quantityContracts,
        side: params.side,
        order_type: params.orderType,
        limit_price: params.price ? params.price.toString() : undefined,
        client_order_id: order.clientOrderId,
      });

      const exchangeOrderId = apiResponse.id ? apiResponse.id.toString() : undefined;
      const apiStatus = apiResponse.state;
      const totalSize = typeof apiResponse.size === 'number' ? apiResponse.size : Number(apiResponse.size || 0);
      const unfilledSize = typeof apiResponse.unfilled_size === 'number' ? apiResponse.unfilled_size : Number(apiResponse.unfilled_size || 0);
      const filledSize = Math.max(0, totalSize - unfilledSize);
      const avgPrice = apiResponse.average_fill_price ? parseFloat(apiResponse.average_fill_price) : (params.price || 0);

      let status: InternalOrderStatus = 'OPEN';
      if (apiStatus === 'closed' || (totalSize > 0 && unfilledSize === 0)) status = 'FILLED';
      else if (apiStatus === 'cancelled') status = 'CANCELLED';
      else if (filledSize > 0) status = 'PARTIALLY_FILLED';

      order.exchangeOrderId = exchangeOrderId;
      order.status = status;
      order.filledContracts = filledSize;
      order.averageFillPrice = avgPrice;
      order.updatedAt = Date.now();

      this.orderRepo.updateOrder(order.id, {
        exchangeOrderId,
        status,
        filledContracts: filledSize,
        averageFillPrice: avgPrice,
      });

      if (status === 'FILLED' || status === 'PARTIALLY_FILLED') {
        const notional = Precision.calculateNotional(filledSize, avgPrice, params.contractValue ?? 0.001);
        const fee = new Decimal(notional).times(this.takerFeeRate).toNumber();

        this.tradeRepo.recordTrade({
          id: nanoid(),
          strategyId: order.strategyId,
          orderId: order.id,
          productId: order.productId,
          symbol: order.symbol,
          side: order.side,
          price: avgPrice,
          quantityContracts: filledSize,
          notionalUsd: notional,
          feesUsd: fee,
          timestamp: Date.now(),
        });
      }

      logger.info(
        {
          orderId: order.id,
          exchangeOrderId,
          status,
          symbol: order.symbol,
          side: order.side,
          contracts: order.quantityContracts,
          purpose: order.purpose,
        },
        'Live order executed on Delta Exchange'
      );

      return order;
    } catch (err: any) {
      const errorMsg = err.message || 'Unknown error placing order';
      logger.error({ err: errorMsg, orderId: order.id }, 'Failed to place live order on Delta');
      order.status = 'REJECTED';
      order.error = errorMsg;
      this.orderRepo.updateOrder(order.id, { status: 'REJECTED', error: errorMsg });

      alerts.warn('Order Placement Failed', `Order ${order.id} for ${order.symbol} rejected: ${errorMsg}`);
      return order;
    }
  }

  /**
   * Cancel an order
   */
  public async cancelOrder(orderId: string): Promise<boolean> {
    const order = this.orderRepo.getOrderById(orderId);
    if (!order) return false;

    if (order.status === 'FILLED' || order.status === 'CANCELLED') {
      return true;
    }

    if (this.mode === 'PAPER' || this.mode === 'BACKTEST') {
      this.orderRepo.updateOrder(orderId, { status: 'CANCELLED' });
      logger.info({ orderId, symbol: order.symbol }, 'Paper order cancelled');
      return true;
    }

    // LIVE cancellation
    if (order.exchangeOrderId) {
      try {
        await this.deltaClient.cancelOrder(order.productId, order.exchangeOrderId);
        this.orderRepo.updateOrder(orderId, { status: 'CANCELLED' });
        logger.info({ orderId, exchangeOrderId: order.exchangeOrderId }, 'Live order cancelled');
        return true;
      } catch (err: any) {
        logger.error({ err: err.message, orderId }, 'Failed to cancel live order on Delta');
        return false;
      }
    }

    return false;
  }

  /**
   * Cancel all open grid orders for a strategy
   */
  public async cancelAllOpenGridOrders(strategyId: string): Promise<number> {
    const openOrders = this.orderRepo.getOrdersByStrategy(strategyId, 'OPEN');
    const gridOrders = openOrders.filter((o) => o.purpose === 'GRID_ENTRY');
    let cancelledCount = 0;

    for (const o of gridOrders) {
      const ok = await this.cancelOrder(o.id);
      if (ok) cancelledCount++;
    }

    logger.info({ strategyId, cancelledCount, total: gridOrders.length }, 'Cancelled pending grid orders');
    return cancelledCount;
  }

  /**
   * Check and simulate fills for OPEN limit orders in PAPER mode based on current prices
   * Returns filled orders
   */
  public checkPaperLimitOrderFills(
    strategyId: string,
    primaryPrice: number,
    hedgePrice: number,
    contractValue: number = 0.001
  ): InternalOrder[] {
    if (this.mode !== 'PAPER' && this.mode !== 'BACKTEST') {
      return [];
    }

    const openOrders = this.orderRepo.getOrdersByStrategy(strategyId, 'OPEN');
    const filledOrders: InternalOrder[] = [];

    for (const order of openOrders) {
      if (order.orderType !== 'limit_order' || !order.price) continue;

      const currentPrice = order.symbol.includes('PAXG') ? hedgePrice : primaryPrice;
      if (currentPrice <= 0) continue;

      let isTriggered = false;
      if (order.side === 'buy' && currentPrice <= order.price) {
        // Buy limit fills when market price falls to or below limit
        isTriggered = true;
      } else if (order.side === 'sell' && currentPrice >= order.price) {
        // Sell limit fills when market price rises to or above limit
        isTriggered = true;
      }

      if (isTriggered) {
        const fillPrice = order.price;
        const notional = Precision.calculateNotional(order.quantityContracts, fillPrice, contractValue);
        const fee = new Decimal(notional).times(this.makerFeeRate).toNumber();

        order.status = 'FILLED';
        order.filledContracts = order.quantityContracts;
        order.averageFillPrice = fillPrice;
        order.updatedAt = Date.now();

        this.orderRepo.updateOrder(order.id, {
          status: 'FILLED',
          filledContracts: order.quantityContracts,
          averageFillPrice: fillPrice,
        });

        this.tradeRepo.recordTrade({
          id: nanoid(),
          strategyId: order.strategyId,
          orderId: order.id,
          productId: order.productId,
          symbol: order.symbol,
          side: order.side,
          price: fillPrice,
          quantityContracts: order.quantityContracts,
          notionalUsd: notional,
          feesUsd: fee,
          timestamp: Date.now(),
        });

        logger.info(
          {
            orderId: order.id,
            symbol: order.symbol,
            side: order.side,
            purpose: order.purpose,
            limitPrice: order.price,
            fillPrice,
            quantity: order.quantityContracts,
          },
          'Paper Limit Order FILLED by price movement'
        );

        filledOrders.push(order);
      }
    }

    return filledOrders;
  }
}

