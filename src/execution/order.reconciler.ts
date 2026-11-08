import { DeltaClient } from '../exchange/delta.client';
import { OrderRepository } from '../database/repositories/order.repository';
import { PositionRepository } from '../database/repositories/position.repository';
import { InternalOrderStatus } from '../types/order.types';
import { logger } from '../monitoring/logger';
import { alerts } from '../monitoring/alerts';

export class OrderReconciler {
  private deltaClient: DeltaClient;
  private orderRepo: OrderRepository;
  private positionRepo: PositionRepository;
  private strategyId: string;

  constructor(
    deltaClient: DeltaClient,
    orderRepo: OrderRepository,
    positionRepo: PositionRepository,
    strategyId: string
  ) {
    this.deltaClient = deltaClient;
    this.orderRepo = orderRepo;
    this.positionRepo = positionRepo;
    this.strategyId = strategyId;
  }

  /**
   * Reconcile local open orders against Delta Exchange
   */
  public async reconcileOrders(): Promise<void> {
    try {
      const localOpenOrders = this.orderRepo.getOrdersByStrategy(this.strategyId, 'OPEN');
      if (localOpenOrders.length === 0) return;

      const exchangeOrders = await this.deltaClient.getOpenOrders();
      const exchangeOrderMap = new Map<string, any>();

      for (const eo of exchangeOrders) {
        if (eo.id) exchangeOrderMap.set(eo.id.toString(), eo);
        if (eo.client_order_id) exchangeOrderMap.set(eo.client_order_id, eo);
      }

      for (const lo of localOpenOrders) {
        if (!lo.exchangeOrderId && !lo.clientOrderId) continue;

        const remote = (lo.exchangeOrderId && exchangeOrderMap.get(lo.exchangeOrderId)) ||
                       exchangeOrderMap.get(lo.clientOrderId);

        if (!remote) {
          // Order no longer open on exchange: could be filled or cancelled
          // Check order status specifically
          if (lo.exchangeOrderId) {
            try {
              const statusRes = await this.deltaClient.getOrderStatus(lo.productId, lo.exchangeOrderId);
              if (statusRes.state === 'closed') {
                this.orderRepo.updateOrder(lo.id, {
                  status: 'FILLED',
                  filledContracts: lo.quantityContracts,
                  averageFillPrice: statusRes.average_fill_price ? parseFloat(statusRes.average_fill_price) : lo.price,
                });
                logger.info({ orderId: lo.id }, 'Reconciled order: confirmed FILLED');
              } else if (statusRes.state === 'cancelled') {
                this.orderRepo.updateOrder(lo.id, { status: 'CANCELLED' });
                logger.info({ orderId: lo.id }, 'Reconciled order: confirmed CANCELLED');
              }
            } catch {
              // Ignore single lookup failure
            }
          }
        } else {
          // Update partial fills if applicable
          const unfilled = remote.unfilled_size ? parseInt(remote.unfilled_size, 10) : 0;
          const totalSize = remote.size ? parseInt(remote.size, 10) : lo.quantityContracts;
          const filled = totalSize - unfilled;

          if (filled > lo.filledContracts) {
            this.orderRepo.updateOrder(lo.id, {
              filledContracts: filled,
              status: filled >= totalSize ? 'FILLED' : 'PARTIALLY_FILLED',
            });
          }
        }
      }
    } catch (err: any) {
      logger.error({ err: err.message }, 'Failed to reconcile orders with Delta');
    }
  }

  /**
   * Reconcile local positions against Delta Exchange
   */
  public async reconcilePositions(): Promise<void> {
    try {
      const exchangePositions = await this.deltaClient.getPositions();
      const localPositions = this.positionRepo.getOpenPositions(this.strategyId);

      for (const lp of localPositions) {
        const ep = exchangePositions.find((p: any) => p.product_id === lp.productId);
        const epSize = ep?.size ? Math.abs(Number(ep.size)) : 0;

        if (Math.abs(epSize - lp.quantityContracts) > 0) {
          logger.warn(
            {
              productId: lp.productId,
              localContracts: lp.quantityContracts,
              exchangeSize: epSize,
            },
            'Position discrepancy detected between local and exchange'
          );
          alerts.warn(
            'Position Mismatch',
            `Product ${lp.productId}: local ${lp.quantityContracts} vs exchange ${epSize}`
          );
        }
      }
    } catch (err: any) {
      logger.error({ err: err.message }, 'Failed to reconcile positions with Delta');
    }
  }
}

