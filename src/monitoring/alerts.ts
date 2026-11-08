/**
 * System Alerts & Notification Dispatcher
 */

import { logger } from './logger';

export type AlertSeverity = 'INFO' | 'WARNING' | 'CRITICAL';

export interface AlertEvent {
  title: string;
  message: string;
  severity: AlertSeverity;
  strategyId?: string;
  metadata?: Record<string, any>;
  timestamp: string;
}

export class AlertService {
  private static listeners: ((alert: AlertEvent) => void)[] = [];

  public static onAlert(listener: (alert: AlertEvent) => void): void {
    this.listeners.push(listener);
  }

  public static notify(title: string, message: string, severity: AlertSeverity = 'INFO', metadata?: Record<string, any>): void {
    const alert: AlertEvent = {
      title,
      message,
      severity,
      metadata,
      timestamp: new Date().toISOString(),
    };

    if (severity === 'CRITICAL') {
      logger.error({ alert }, `🚨 [CRITICAL ALERT] ${title}: ${message}`);
    } else if (severity === 'WARNING') {
      logger.warn({ alert }, `⚠️ [WARNING] ${title}: ${message}`);
    } else {
      logger.info({ alert }, `ℹ️ [INFO] ${title}: ${message}`);
    }

    for (const listener of this.listeners) {
      try {
        listener(alert);
      } catch (err: any) {
        logger.error(`Alert listener error: ${err.message}`);
      }
    }
  }
}

export const alerts = {
  info: (title: string, message: string, metadata?: Record<string, any>) => AlertService.notify(title, message, 'INFO', metadata),
  warn: (title: string, message: string, metadata?: Record<string, any>) => AlertService.notify(title, message, 'WARNING', metadata),
  critical: (title: string, message: string, metadata?: Record<string, any>) => AlertService.notify(title, message, 'CRITICAL', metadata),
  onAlert: (listener: (alert: AlertEvent) => void) => AlertService.onAlert(listener),
};
