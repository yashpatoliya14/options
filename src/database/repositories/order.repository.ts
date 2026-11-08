/**
 * Order Repository for SQLite
 */

import { getDatabase } from '../connection';
import { InternalOrder, InternalOrderStatus, OrderPurpose } from '../../types/order.types';

export class OrderRepository {
  private db = getDatabase();

  public saveOrder(order: InternalOrder): void {
    const stmt = this.db.prepare(`
      INSERT INTO orders (
        id, strategy_id, exchange_order_id, client_order_id,
        product_id, side, order_type, price, quantity,
        filled_quantity, status, purpose, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        exchange_order_id = excluded.exchange_order_id,
        filled_quantity = excluded.filled_quantity,
        status = excluded.status,
        updated_at = excluded.updated_at
    `);

    const now = new Date(order.createdAt).toISOString();
    const updated = new Date(order.updatedAt).toISOString();

    stmt.run(
      order.id,
      order.strategyId,
      order.exchangeOrderId || null,
      order.clientOrderId,
      order.productId,
      order.side,
      order.orderType,
      order.price || null,
      order.quantityContracts,
      order.filledContracts,
      order.status,
      order.purpose,
      now,
      updated
    );
  }

  public createOrder(order: InternalOrder): void {
    this.saveOrder(order);
  }

  public updateOrder(
    orderId: string,
    updates: {
      status?: InternalOrderStatus;
      filledContracts?: number;
      averageFillPrice?: number;
      exchangeOrderId?: string;
      error?: string;
    }
  ): void {
    const now = new Date().toISOString();
    const current: any = this.db.prepare('SELECT * FROM orders WHERE id = ?').get(orderId);
    if (!current) return;

    this.db.prepare(`
      UPDATE orders SET
        status = COALESCE(?, status),
        filled_quantity = COALESCE(?, filled_quantity),
        exchange_order_id = COALESCE(?, exchange_order_id),
        updated_at = ?
      WHERE id = ?
    `).run(
      updates.status || null,
      updates.filledContracts !== undefined ? updates.filledContracts : null,
      updates.exchangeOrderId || null,
      now,
      orderId
    );
  }

  public updateOrderStatus(
    clientOrderId: string,
    status: InternalOrderStatus,
    filledContracts?: number,
    averageFillPrice?: number,
    exchangeOrderId?: string
  ): void {
    const now = new Date().toISOString();
    const current: any = this.db.prepare('SELECT * FROM orders WHERE client_order_id = ?').get(clientOrderId);
    if (!current) return;

    this.db.prepare(`
      UPDATE orders SET
        status = ?,
        filled_quantity = ?,
        exchange_order_id = COALESCE(?, exchange_order_id),
        updated_at = ?
      WHERE client_order_id = ?
    `).run(
      status,
      filledContracts !== undefined ? filledContracts : current.filled_quantity,
      exchangeOrderId || null,
      now,
      clientOrderId
    );
  }

  public getOrderById(orderId: string): InternalOrder | null {
    const row: any = this.db.prepare('SELECT * FROM orders WHERE id = ?').get(orderId);
    if (!row) return null;
    return this.mapOrder(row);
  }

  public getOrderByClientId(clientOrderId: string): InternalOrder | null {
    const row: any = this.db.prepare('SELECT * FROM orders WHERE client_order_id = ?').get(clientOrderId);
    if (!row) return null;
    return this.mapOrder(row);
  }

  public getOpenOrdersByPurpose(strategyId: string, purpose: OrderPurpose): InternalOrder[] {
    const rows: any[] = this.db.prepare(`
      SELECT * FROM orders
      WHERE strategy_id = ? AND purpose = ? AND status IN ('PENDING', 'SUBMITTED', 'OPEN')
    `).all(strategyId, purpose);

    return rows.map(this.mapOrder);
  }

  public getOrdersByStrategy(strategyId: string, status?: InternalOrderStatus, limit: number = 100): InternalOrder[] {
    let rows: any[];
    if (status) {
      rows = this.db.prepare(`
        SELECT * FROM orders
        WHERE strategy_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT ?
      `).all(strategyId, status, limit);
    } else {
      rows = this.db.prepare(`
        SELECT * FROM orders
        WHERE strategy_id = ?
        ORDER BY created_at DESC
        LIMIT ?
      `).all(strategyId, limit);
    }

    return rows.map(this.mapOrder);
  }

  public cancelPendingOrdersByPurpose(strategyId: string, purpose: OrderPurpose): void {
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE orders SET status = 'CANCELLED', updated_at = ?
      WHERE strategy_id = ? AND purpose = ? AND status IN ('PENDING', 'SUBMITTED', 'OPEN')
    `).run(now, strategyId, purpose);
  }

  private mapOrder(row: any): InternalOrder {
    return {
      id: row.id,
      strategyId: row.strategy_id,
      exchangeOrderId: row.exchange_order_id || undefined,
      clientOrderId: row.client_order_id,
      productId: row.product_id,
      symbol: row.product_id === 123006 ? 'PAXGUSD' : 'XAUTUSD',
      side: row.side as 'buy' | 'sell',
      orderType: row.order_type as 'limit_order' | 'market_order',
      price: row.price || undefined,
      quantityContracts: row.quantity,
      filledContracts: row.filled_quantity,
      status: row.status as InternalOrderStatus,
      purpose: row.purpose as OrderPurpose,
      createdAt: new Date(row.created_at).getTime(),
      updatedAt: new Date(row.updated_at).getTime(),
    };
  }
}
