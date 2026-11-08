import { nanoid } from 'nanoid';
import { RiskLimits } from '../types/strategy.types';
import { PortfolioSummary } from './position.manager';
import { TradeRepository } from '../database/repositories/trade.repository';
import { RiskRepository } from '../database/repositories/risk.repository';
import { alerts } from '../monitoring/alerts';
import { logger } from '../monitoring/logger';

export interface RiskCheckResult {
  allowed: boolean;
  reason?: string;
  breachedLimit?: string;
}

export class RiskManager {
  private limits: RiskLimits;
  private tradeRepo: TradeRepository;
  private riskRepo: RiskRepository;
  private strategyId: string;
  private emergencyStopped: boolean = false;
  private emergencyReason: string = '';
  private peakEquity: number = 0;

  constructor(
    limits: RiskLimits,
    tradeRepo: TradeRepository,
    riskRepo: RiskRepository,
    strategyId: string
  ) {
    this.limits = limits;
    this.tradeRepo = tradeRepo;
    this.riskRepo = riskRepo;
    this.strategyId = strategyId;
  }

  public isEmergencyStopped(): boolean {
    return this.emergencyStopped;
  }

  public getEmergencyReason(): string {
    return this.emergencyReason;
  }

  public resetEmergencyStop(): void {
    this.emergencyStopped = false;
    this.emergencyReason = '';
    logger.info({ strategyId: this.strategyId }, 'Emergency stop manually reset');
  }

  /**
   * Pre-trade safety validation before placing an order (Lot-based)
   */
  public validatePreTrade(
    targetInstrument: 'PRIMARY' | 'HEDGE',
    proposedLots: number,
    currentPrimaryLots: number,
    currentHedgeLots: number
  ): RiskCheckResult {
    if (this.emergencyStopped) {
      return {
        allowed: false,
        reason: `Emergency stop active: ${this.emergencyReason}`,
        breachedLimit: 'EMERGENCY_STOP',
      };
    }

    // 1. Primary lots cap
    if (targetInstrument === 'PRIMARY') {
      const newPrimaryLots = currentPrimaryLots + proposedLots;
      if (newPrimaryLots > this.limits.maxPrimaryLots) {
        return {
          allowed: false,
          reason: `Proposed primary lots ${newPrimaryLots} exceeds limit ${this.limits.maxPrimaryLots}`,
          breachedLimit: 'MAX_PRIMARY_LOTS',
        };
      }
    }

    // 2. Hedge lots cap
    if (targetInstrument === 'HEDGE') {
      const newHedgeLots = currentHedgeLots + proposedLots;
      if (newHedgeLots > this.limits.maxHedgeLots) {
        return {
          allowed: false,
          reason: `Proposed hedge lots ${newHedgeLots} exceeds limit ${this.limits.maxHedgeLots}`,
          breachedLimit: 'MAX_HEDGE_LOTS',
        };
      }
    }

    // 3. Total combined lots cap
    const totalLots = currentPrimaryLots + currentHedgeLots + proposedLots;
    if (totalLots > this.limits.maxTotalLots) {
      return {
        allowed: false,
        reason: `Total lots ${totalLots} exceeds max total lots limit ${this.limits.maxTotalLots}`,
        breachedLimit: 'MAX_TOTAL_LOTS',
      };
    }

    return { allowed: true };
  }

  /**
   * Periodic portfolio risk checks: daily loss, max drawdown
   */
  public evaluatePortfolioRisk(summary: PortfolioSummary, accountEquityUsd: number = 1000): RiskCheckResult {
    if (this.emergencyStopped) {
      return { allowed: false, reason: this.emergencyReason };
    }

    // Update peak equity for drawdown tracking
    if (accountEquityUsd > this.peakEquity) {
      this.peakEquity = accountEquityUsd;
    }

    // 1. Daily Loss Check
    const today = new Date().toISOString().split('T')[0];
    const todayPerf = this.tradeRepo.getDailyPerformance(this.strategyId, today);
    const todayRealizedPnl = todayPerf?.realised_pnl || 0;
    const currentDayLoss = -(todayRealizedPnl + summary.combinedUnrealizedPnl);

    if (currentDayLoss > this.limits.maxDailyLossUsd) {
      this.triggerEmergencyStop(
        `Max daily loss exceeded: -$${currentDayLoss.toFixed(2)} vs limit $${this.limits.maxDailyLossUsd}`
      );
      return {
        allowed: false,
        reason: `Daily loss limit breached`,
        breachedLimit: 'MAX_DAILY_LOSS',
      };
    }

    // 2. Max Drawdown Check
    if (this.peakEquity > 0) {
      const currentEquity = accountEquityUsd + summary.combinedUnrealizedPnl;
      const drawdownPercent = ((this.peakEquity - currentEquity) / this.peakEquity) * 100;

      if (drawdownPercent > this.limits.maxDrawdownPercent) {
        this.triggerEmergencyStop(
          `Max drawdown exceeded: ${drawdownPercent.toFixed(2)}% vs limit ${this.limits.maxDrawdownPercent}%`
        );
        return {
          allowed: false,
          reason: `Max drawdown limit breached`,
          breachedLimit: 'MAX_DRAWDOWN',
        };
      }
    }

    // 3. Max Open Hedges Check
    if (summary.openHedgeCount > this.limits.maxOpenHedges) {
      this.recordWarning(
        'MAX_OPEN_HEDGES',
        `Open hedges count (${summary.openHedgeCount}) reached limit (${this.limits.maxOpenHedges})`
      );
    }

    return { allowed: true };
  }

  /**
   * Trigger emergency stop
   */
  public triggerEmergencyStop(reason: string): void {
    this.emergencyStopped = true;
    this.emergencyReason = reason;

    this.riskRepo.recordRiskEvent({
      id: nanoid(),
      strategyId: this.strategyId,
      eventType: 'EMERGENCY_STOP',
      description: reason,
      severity: 'CRITICAL',
      createdAt: Date.now(),
    });

    alerts.critical('EMERGENCY STOP TRIGGERED', reason);
    logger.error({ strategyId: this.strategyId, reason }, 'EMERGENCY STOP ACTIVATED');
  }

  private recordWarning(eventType: string, description: string): void {
    this.riskRepo.recordRiskEvent({
      id: nanoid(),
      strategyId: this.strategyId,
      eventType,
      description,
      severity: 'WARNING',
      createdAt: Date.now(),
    });
    alerts.warn(eventType, description);
  }
}
