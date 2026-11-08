/**
 * Hedge Repository for SQLite
 */

import { getDatabase } from '../connection';
import { HedgePositionState, PositionSide } from '../../types/strategy.types';

export class HedgeRepository {
  private db = getDatabase();

  public saveHedgeEntry(hedge: HedgePositionState): void {
    const stmt = this.db.prepare(`
      INSERT INTO hedge_entries (
        id, strategy_id, trigger_level, product_id, side,
        entry_price, quantity, target_price, realised_pnl, status,
        order_id, take_profit_order_id, created_at, closed_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        entry_price = excluded.entry_price,
        target_price = excluded.target_price,
        realised_pnl = excluded.realised_pnl,
        status = excluded.status,
        order_id = COALESCE(excluded.order_id, hedge_entries.order_id),
        take_profit_order_id = COALESCE(excluded.take_profit_order_id, hedge_entries.take_profit_order_id),
        closed_at = excluded.closed_at
    `);

    const created = new Date(hedge.createdAt).toISOString();
    const closed = hedge.closedAt ? new Date(hedge.closedAt).toISOString() : null;

    stmt.run(
      hedge.id,
      hedge.strategyId,
      hedge.triggerLevel,
      123006, // PAXGUSD
      hedge.side,
      hedge.entryPrice,
      hedge.quantityContracts,
      hedge.targetPrice,
      hedge.realizedPnlUsd || 0,
      hedge.status,
      hedge.orderId || null,
      hedge.takeProfitOrderId || null,
      created,
      closed
    );
  }

  public getOpenHedges(strategyId: string): HedgePositionState[] {
    const rows: any[] = this.db.prepare(`
      SELECT * FROM hedge_entries
      WHERE strategy_id = ? AND status IN ('PENDING', 'OPEN', 'TAKE_PROFIT_PLACED')
      ORDER BY trigger_level ASC
    `).all(strategyId);

    return rows.map(this.mapHedge);
  }

  public getHedgeById(hedgeId: string): HedgePositionState | null {
    const row: any = this.db.prepare('SELECT * FROM hedge_entries WHERE id = ?').get(hedgeId);
    if (!row) return null;
    return this.mapHedge(row);
  }

  public getHedgeByLevel(strategyId: string, level: number): HedgePositionState | null {
    const row: any = this.db.prepare(`
      SELECT * FROM hedge_entries
      WHERE strategy_id = ? AND trigger_level = ? AND status IN ('PENDING', 'OPEN', 'TAKE_PROFIT_PLACED')
    `).get(strategyId, level);

    if (!row) return null;
    return this.mapHedge(row);
  }

  public updateHedge(
    hedgeId: string,
    updates: {
      status?: 'PENDING' | 'OPEN' | 'TAKE_PROFIT_PLACED' | 'CLOSED' | 'CANCELLED';
      orderId?: string;
      takeProfitOrderId?: string;
      entryPrice?: number;
      targetPrice?: number;
      realizedPnlUsd?: number;
      closedAt?: number;
    }
  ): void {
    const closed = updates.closedAt ? new Date(updates.closedAt).toISOString() : null;
    this.db.prepare(`
      UPDATE hedge_entries SET
        status = COALESCE(?, status),
        order_id = COALESCE(?, order_id),
        take_profit_order_id = COALESCE(?, take_profit_order_id),
        entry_price = COALESCE(?, entry_price),
        target_price = COALESCE(?, target_price),
        realised_pnl = COALESCE(?, realised_pnl),
        closed_at = COALESCE(?, closed_at)
      WHERE id = ?
    `).run(
      updates.status || null,
      updates.orderId || null,
      updates.takeProfitOrderId || null,
      updates.entryPrice || null,
      updates.targetPrice || null,
      updates.realizedPnlUsd !== undefined ? updates.realizedPnlUsd : null,
      closed,
      hedgeId
    );
  }

  public closeHedge(hedgeId: string, realizedPnlUsd: number): void {
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE hedge_entries SET
        status = 'CLOSED',
        realised_pnl = ?,
        closed_at = ?
      WHERE id = ?
    `).run(realizedPnlUsd, now, hedgeId);
  }

  public closeAllHedges(strategyId: string): void {
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE hedge_entries SET
        status = 'CANCELLED',
        closed_at = ?
      WHERE strategy_id = ? AND status IN ('PENDING', 'OPEN', 'TAKE_PROFIT_PLACED')
    `).run(now, strategyId);
  }

  public getTotalRealizedHedgePnl(strategyId: string): number {
    const row: any = this.db.prepare(`
      SELECT SUM(realised_pnl) as total_pnl
      FROM hedge_entries
      WHERE strategy_id = ? AND status = 'CLOSED'
    `).get(strategyId);

    return row?.total_pnl || 0;
  }

  public getTotalRealizedPnl(strategyId: string): number {
    return this.getTotalRealizedHedgePnl(strategyId);
  }

  private mapHedge(row: any): HedgePositionState {
    return {
      id: row.id,
      strategyId: row.strategy_id,
      triggerLevel: row.trigger_level,
      triggerPrice: 0,
      instrument: 'PAXGUSD',
      side: row.side as PositionSide,
      entryPrice: row.entry_price,
      targetPrice: row.target_price,
      quantityContracts: row.quantity,
      notionalUsd: 0,
      status: row.status,
      orderId: row.order_id || undefined,
      takeProfitOrderId: row.take_profit_order_id || undefined,
      realizedPnlUsd: row.realised_pnl,
      createdAt: new Date(row.created_at).getTime(),
      closedAt: row.closed_at ? new Date(row.closed_at).getTime() : undefined,
    };
  }
}
