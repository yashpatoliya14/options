/**
 * Risk Repository for SQLite
 */

import { getDatabase } from '../connection';

export interface RiskEventRow {
  id: string;
  strategyId: string;
  eventType: string;
  description: string;
  severity: 'INFO' | 'WARNING' | 'CRITICAL';
  createdAt: number;
}

export class RiskRepository {
  private db = getDatabase();

  public recordRiskEvent(event: RiskEventRow): void {
    const stmt = this.db.prepare(`
      INSERT INTO risk_events (id, strategy_id, event_type, description, severity, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    `);

    const now = new Date(event.createdAt).toISOString();
    stmt.run(event.id, event.strategyId, event.eventType, event.description, event.severity, now);
  }

  public getRiskEvents(strategyId: string, limit: number = 50): RiskEventRow[] {
    const rows: any[] = this.db.prepare(`
      SELECT * FROM risk_events
      WHERE strategy_id = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).all(strategyId, limit);

    return rows.map((r) => ({
      id: r.id,
      strategyId: r.strategy_id,
      eventType: r.event_type,
      description: r.description,
      severity: r.severity as 'INFO' | 'WARNING' | 'CRITICAL',
      createdAt: new Date(r.created_at).getTime(),
    }));
  }
}

