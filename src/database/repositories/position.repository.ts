/**
 * Position Repository for SQLite
 */

import { getDatabase } from '../connection';
import { InternalPosition } from '../../types/order.types';
import { PositionSide } from '../../types/strategy.types';

export class PositionRepository {
  private db = getDatabase();

  public savePosition(pos: InternalPosition): void {
    const now = new Date(pos.updatedAt).toISOString();
    const id = `pos-${pos.strategyId}-${pos.productId}`;

    const stmt = this.db.prepare(`
      INSERT INTO positions (
        id, strategy_id, product_id, side, quantity,
        average_entry_price, unrealized_pnl, status, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        side = excluded.side,
        quantity = excluded.quantity,
        average_entry_price = excluded.average_entry_price,
        unrealized_pnl = excluded.unrealized_pnl,
        status = excluded.status,
        updated_at = excluded.updated_at
    `);

    const status = pos.quantityContracts > 0 ? 'OPEN' : 'CLOSED';

    stmt.run(
      id,
      pos.strategyId,
      pos.productId,
      pos.side,
      pos.quantityContracts,
      pos.averageEntryPrice,
      pos.unrealizedPnlUsd,
      status,
      now
    );
  }

  public upsertPosition(pos: InternalPosition): void {
    this.savePosition(pos);
  }

  public updatePnL(strategyId: string, productId: number, markPrice: number, unrealizedPnl: number): void {
    const id = `pos-${strategyId}-${productId}`;
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE positions SET
        unrealized_pnl = ?,
        updated_at = ?
      WHERE id = ?
    `).run(unrealizedPnl, now, id);
  }

  public getPosition(strategyId: string, productId: number): InternalPosition | null {
    const row: any = this.db.prepare(`
      SELECT * FROM positions
      WHERE strategy_id = ? AND product_id = ? AND status = 'OPEN'
    `).get(strategyId, productId);

    if (!row) return null;

    return {
      strategyId: row.strategy_id,
      productId: row.product_id,
      symbol: row.product_id === 123006 ? 'PAXGUSD' : 'XAUTUSD',
      side: row.side as PositionSide,
      quantityContracts: row.quantity,
      averageEntryPrice: row.average_entry_price,
      currentMarkPrice: 0,
      unrealizedPnlUsd: row.unrealized_pnl,
      realizedPnlUsd: 0,
      leverage: 10,
      updatedAt: new Date(row.updated_at).getTime(),
    };
  }

  public getOpenPositions(strategyId: string): InternalPosition[] {
    const rows: any[] = this.db.prepare(`
      SELECT * FROM positions
      WHERE strategy_id = ? AND status = 'OPEN' AND quantity > 0
    `).all(strategyId);

    return rows.map((r) => ({
      strategyId: r.strategy_id,
      productId: r.product_id,
      symbol: r.product_id === 123006 ? 'PAXGUSD' : 'XAUTUSD',
      side: r.side as PositionSide,
      quantityContracts: r.quantity,
      averageEntryPrice: r.average_entry_price,
      currentMarkPrice: 0,
      unrealizedPnlUsd: r.unrealized_pnl,
      realizedPnlUsd: 0,
      leverage: 10,
      updatedAt: new Date(r.updated_at).getTime(),
    }));
  }

  public closePosition(strategyId: string, productId: number): void {
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE positions SET status = 'CLOSED', quantity = 0, unrealized_pnl = 0, updated_at = ?
      WHERE strategy_id = ? AND product_id = ?
    `).run(now, strategyId, productId);
  }
}
