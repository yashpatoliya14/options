/**
 * Trade & Performance SQLite Repository
 */

import { getDatabase } from '../connection';
import { TradeRecord } from '../../types/order.types';

export class TradeRepository {
  private db = getDatabase();

  public recordTrade(trade: TradeRecord): void {
    const stmt = this.db.prepare(`
      INSERT INTO trades (
        id, strategy_id, order_id, product_id, side,
        entry_price, exit_price, quantity, realised_pnl, fees,
        created_at, closed_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const now = new Date(trade.timestamp).toISOString();

    stmt.run(
      trade.id,
      trade.strategyId,
      trade.orderId,
      trade.productId,
      trade.side,
      trade.price,
      null,
      trade.quantityContracts,
      trade.realizedPnlUsd || 0,
      trade.feesUsd,
      now,
      null
    );

    // Also update daily performance
    const dateStr = now.split('T')[0];
    this.updateDailyPerformance(trade.strategyId, dateStr, trade.realizedPnlUsd || 0, trade.feesUsd);
  }

  public getTrades(strategyId: string, limit: number = 100): any[] {
    return this.db.prepare(`
      SELECT * FROM trades
      WHERE strategy_id = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(strategyId, limit);
  }

  public updateDailyPerformance(strategyId: string, date: string, pnlDelta: number, feeDelta: number): void {
    const now = new Date().toISOString();
    const id = `perf-${strategyId}-${date}`;

    const stmt = this.db.prepare(`
      INSERT INTO daily_performance (id, strategy_id, date, realised_pnl, unrealised_pnl, fees, funding_cost, total_trades, created_at)
      VALUES (?, ?, ?, ?, 0, ?, 0, 1, ?)
      ON CONFLICT(strategy_id, date) DO UPDATE SET
        realised_pnl = daily_performance.realised_pnl + excluded.realised_pnl,
        fees = daily_performance.fees + excluded.fees,
        total_trades = daily_performance.total_trades + 1
    `);

    stmt.run(id, strategyId, date, pnlDelta, feeDelta, now);
  }

  public getDailyPerformance(strategyId: string, date: string): any {
    return this.db.prepare(`
      SELECT * FROM daily_performance
      WHERE strategy_id = ? AND date = ?
    `).get(strategyId, date);
  }

  public getTotalPerformance(strategyId: string): { totalRealizedPnl: number; totalFees: number; totalTrades: number } {
    const row: any = this.db.prepare(`
      SELECT
        COALESCE(SUM(realised_pnl), 0) as total_pnl,
        COALESCE(SUM(fees), 0) as total_fees,
        COALESCE(SUM(total_trades), 0) as total_trades
      FROM daily_performance
      WHERE strategy_id = ?
    `).get(strategyId);

    return {
      totalRealizedPnl: row?.total_pnl || 0,
      totalFees: row?.total_fees || 0,
      totalTrades: row?.total_trades || 0,
    };
  }
}

