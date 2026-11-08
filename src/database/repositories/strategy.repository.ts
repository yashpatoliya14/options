/**
 * Strategy & Market Data SQLite Repository
 */

import { getDatabase } from '../connection';
import { StrategyState, StrategyStatus, TrendDirection, StrategyConfig } from '../../types/strategy.types';
import { DeltaCandle } from '../../types/exchange.types';

export class StrategyRepository {
  private db = getDatabase();

  public getStrategy(id: string): any {
    const row = this.db.prepare('SELECT * FROM strategies WHERE id = ?').get(id);
    return row || null;
  }

  public updateStrategy(id: string, updates: { status?: StrategyStatus; mode?: string }): void {
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE strategies SET
        status = COALESCE(?, status),
        mode = COALESCE(?, mode),
        updated_at = ?
      WHERE id = ?
    `).run(updates.status || null, updates.mode || null, now, id);
  }

  public updateStrategyStatus(id: string, status: StrategyStatus): void {
    this.updateStrategy(id, { status });
  }

  public getStrategyState(strategyId: string): StrategyState | null {
    const row: any = this.db.prepare('SELECT * FROM strategy_states WHERE strategy_id = ?').get(strategyId);
    if (!row) return null;

    return {
      strategyId: row.strategy_id,
      currentDirection: row.current_direction as TrendDirection,
      lastProcessedCandleTimestamp: row.last_processed_candle,
      referencePrice: row.reference_price,
      primaryAveragePrice: row.primary_average_price,
      primaryQuantity: row.primary_quantity,
      primaryNotional: 0,
      currentGridLevel: row.current_grid_level,
      lastHedgeTriggerLevel: row.last_hedge_trigger_level,
      updatedAt: new Date(row.updated_at).getTime(),
    };
  }

  public getState(strategyId: string): StrategyState | null {
    return this.getStrategyState(strategyId);
  }

  public updateStrategyState(state: StrategyState): void {
    const now = new Date().toISOString();
    const existing = this.getStrategyState(state.strategyId);

    if (existing) {
      this.db.prepare(`
        UPDATE strategy_states SET
          current_direction = ?,
          last_processed_candle = ?,
          reference_price = ?,
          primary_average_price = ?,
          primary_quantity = ?,
          current_grid_level = ?,
          last_hedge_trigger_level = ?,
          updated_at = ?
        WHERE strategy_id = ?
      `).run(
        state.currentDirection,
        state.lastProcessedCandleTimestamp,
        state.referencePrice,
        state.primaryAveragePrice,
        state.primaryQuantity,
        state.currentGridLevel,
        state.lastHedgeTriggerLevel,
        now,
        state.strategyId
      );
    } else {
      this.db.prepare(`
        INSERT INTO strategy_states (
          strategy_id, current_direction, last_processed_candle, reference_price,
          primary_average_price, primary_quantity, current_grid_level, last_hedge_trigger_level, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        state.strategyId,
        state.currentDirection,
        state.lastProcessedCandleTimestamp,
        state.referencePrice,
        state.primaryAveragePrice,
        state.primaryQuantity,
        state.currentGridLevel,
        state.lastHedgeTriggerLevel,
        now
      );
    }
  }

  public updateState(strategyId: string, updates: Partial<StrategyState>): void {
    const current = this.getStrategyState(strategyId) || {
      strategyId,
      currentDirection: 'NEUTRAL' as TrendDirection,
      lastProcessedCandleTimestamp: 0,
      referencePrice: 0,
      primaryAveragePrice: 0,
      primaryQuantity: 0,
      primaryNotional: 0,
      currentGridLevel: 0,
      lastHedgeTriggerLevel: 0,
      updatedAt: Date.now(),
    };

    const merged: StrategyState = {
      ...current,
      ...updates,
      strategyId,
    };

    this.updateStrategyState(merged);
  }

  public saveCandle(arg1: any, arg2?: any, arg3?: any): void {
    let productId = 131253; // default XAUTUSD
    let timeframe = '4h';
    let candle: any;

    if (arg3) {
      productId = typeof arg1 === 'number' ? arg1 : 131253;
      timeframe = arg2;
      candle = arg3;
    } else if (arg1 && arg1.timestamp) {
      candle = {
        time: arg1.timestamp,
        open: arg1.open,
        high: arg1.high,
        low: arg1.low,
        close: arg1.close,
        volume: arg1.volume || 0,
      };
      timeframe = arg1.timeframe || '4h';
      productId = arg1.symbol?.includes('PAXG') ? 123006 : 131253;
    } else {
      return;
    }

    const stmt = this.db.prepare(`
      INSERT INTO candles (product_id, timeframe, timestamp, open, high, low, close, volume)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(product_id, timeframe, timestamp) DO UPDATE SET
        open = excluded.open,
        high = excluded.high,
        low = excluded.low,
        close = excluded.close,
        volume = excluded.volume
    `);

    stmt.run(
      productId,
      timeframe,
      candle.time,
      candle.open,
      candle.high,
      candle.low,
      candle.close,
      candle.volume
    );
  }

  public saveCandlesBatch(productId: number, timeframe: string, candles: DeltaCandle[]): void {
    const stmt = this.db.prepare(`
      INSERT INTO candles (product_id, timeframe, timestamp, open, high, low, close, volume)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(product_id, timeframe, timestamp) DO UPDATE SET
        open = excluded.open,
        high = excluded.high,
        low = excluded.low,
        close = excluded.close,
        volume = excluded.volume
    `);

    this.db.transaction(() => {
      for (const c of candles) {
        stmt.run(productId, timeframe, c.time, c.open, c.high, c.low, c.close, c.volume);
      }
    })();
  }

  public getLatestCandles(productId: number, timeframe: string, limit: number = 100): DeltaCandle[] {
    const rows: any[] = this.db.prepare(`
      SELECT timestamp as time, open, high, low, close, volume
      FROM candles
      WHERE product_id = ? AND timeframe = ?
      ORDER BY timestamp DESC
      LIMIT ?
    `).all(productId, timeframe, limit);

    return rows.reverse();
  }

  public getLastCandleTimestamp(productId: number, timeframe: string): number {
    const row: any = this.db.prepare(`
      SELECT MAX(timestamp) as last_time
      FROM candles
      WHERE product_id = ? AND timeframe = ?
    `).get(productId, timeframe);

    return row?.last_time || 0;
  }
}
