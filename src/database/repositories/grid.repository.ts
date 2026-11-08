/**
 * Grid Repository for SQLite
 */

import { getDatabase } from '../connection';
import { GridLevelDefinition } from '../../types/strategy.types';

export interface GridLevelRow {
  id: string;
  strategyId: string;
  level: number;
  triggerPrice: number;
  quantity: number;
  orderId?: string;
  status: 'PENDING' | 'PLACED' | 'FILLED' | 'CANCELLED';
  createdAt: number;
}

export class GridRepository {
  private db = getDatabase();

  public saveGridLevel(grid: GridLevelRow): void {
    const stmt = this.db.prepare(`
      INSERT INTO grid_levels (
        id, strategy_id, level, trigger_price, quantity, order_id, status, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        trigger_price = excluded.trigger_price,
        quantity = excluded.quantity,
        order_id = COALESCE(excluded.order_id, grid_levels.order_id),
        status = excluded.status
    `);

    const now = new Date(grid.createdAt).toISOString();

    stmt.run(
      grid.id,
      grid.strategyId,
      grid.level,
      grid.triggerPrice,
      grid.quantity,
      grid.orderId || null,
      grid.status,
      now
    );
  }

  public getGridLevels(strategyId: string): GridLevelRow[] {
    const rows: any[] = this.db.prepare(`
      SELECT * FROM grid_levels
      WHERE strategy_id = ?
      ORDER BY level ASC
    `).all(strategyId);

    return rows.map(this.mapGrid);
  }

  public updateGridStatus(gridId: string, status: 'PENDING' | 'PLACED' | 'FILLED' | 'CANCELLED', orderId?: string): void {
    this.db.prepare(`
      UPDATE grid_levels SET
        status = ?,
        order_id = COALESCE(?, order_id)
      WHERE id = ?
    `).run(status, orderId || null, gridId);
  }

  public clearGridLevels(strategyId: string): void {
    this.db.prepare(`
      UPDATE grid_levels SET status = 'CANCELLED'
      WHERE strategy_id = ? AND status IN ('PENDING', 'PLACED')
    `).run(strategyId);
  }

  public deleteGridLevels(strategyId: string): void {
    this.db.prepare('DELETE FROM grid_levels WHERE strategy_id = ?').run(strategyId);
  }

  private mapGrid(row: any): GridLevelRow {
    return {
      id: row.id,
      strategyId: row.strategy_id,
      level: row.level,
      triggerPrice: row.trigger_price,
      quantity: row.quantity,
      orderId: row.order_id || undefined,
      status: row.status,
      createdAt: new Date(row.created_at).getTime(),
    };
  }
}

