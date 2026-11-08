/**
 * Idempotency Key Generator for Order Execution
 */

import crypto from 'crypto';

export class Idempotency {
  /**
   * Generate unique, deterministic client order ID for an order purpose
   */
  public static generateClientOrderId(prefix: string, strategyId: string, levelOrNonce?: string | number): string {
    const timestamp = Date.now().toString(36);
    const random = crypto.randomBytes(3).toString('hex');
    const suffix = levelOrNonce !== undefined ? `-${levelOrNonce}` : '';
    return `${prefix}-${strategyId.slice(0, 6)}${suffix}-${timestamp}-${random}`.toUpperCase();
  }

  /**
   * Deterministic hedge ID for a specific trigger level to prevent duplicates
   */
  public static getHedgeKey(strategyId: string, triggerLevel: number, cycleTimestamp: number): string {
    return `HEDGE-${strategyId}-${cycleTimestamp}-L${triggerLevel}`;
  }

  /**
   * Deterministic grid level key
   */
  public static getGridKey(strategyId: string, level: number, cycleTimestamp: number): string {
    return `GRID-${strategyId}-${cycleTimestamp}-L${level}`;
  }
}

