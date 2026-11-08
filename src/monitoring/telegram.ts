/**
 * Telegram Notification Service
 * Real-time trade notifications, order fills, hedge profit bookings, and emergency alerts
 */

import axios from 'axios';
import { env } from '../config/env';
import { logger } from './logger';
import { AlertEvent, AlertService } from './alerts';
import { TrendDirection } from '../types/strategy.types';

export interface TrendSignalParams {
  direction: TrendDirection;
  symbol: string;
  initialLots: number;
  price: number;
  gridLevels: number;
  gridLotsPerLevel: number;
  spacingPercent: number;
  leverage: number;
}

export interface OrderFilledParams {
  symbol: string;
  purpose: string;
  side: 'buy' | 'sell';
  lots: number;
  price: number;
  avgPrice?: number;
  totalLots?: number;
  level?: number;
}

export interface HedgeOpenedParams {
  symbol: string;
  level: number;
  side: 'buy' | 'sell';
  lots: number;
  entryPrice: number;
  targetPrice: number;
  triggerPct: number;
  primaryPrice: number;
}

export interface HedgeTakeProfitParams {
  symbol: string;
  level: number;
  exitPrice: number;
  realizedPnl: number;
  fees: number;
  remainingPrimaryLots?: number;
}

export interface TrendReversalParams {
  fromDirection: string;
  toDirection: string;
  primaryLotsClosed: number;
  hedgesClosedCount: number;
  realizedPnl?: number;
}

export interface EmergencyFlattenParams {
  reason: string;
  status: string;
  cancelledOrdersCount: number;
  closedPositions: { symbol: string; size: number }[];
}

export class TelegramService {
  private botToken: string;
  private chatId: string;
  private enabled: boolean;
  private baseUrl: string;

  constructor(botToken?: string, chatId?: string, enabled?: boolean) {
    this.botToken = botToken ?? env.TELEGRAM_BOT_TOKEN;
    this.chatId = chatId ?? env.TELEGRAM_CHAT_ID;
    this.enabled = enabled ?? (env.TELEGRAM_ENABLED && Boolean(this.botToken && this.chatId));
    this.baseUrl = `https://api.telegram.org/bot${this.botToken}`;

    // Auto-hook into AlertService so system alerts are also broadcasted
    AlertService.onAlert((alert: AlertEvent) => {
      this.handleSystemAlert(alert);
    });
  }

  public isEnabled(): boolean {
    return this.enabled && Boolean(this.botToken && this.chatId);
  }

  /**
   * Send a formatted HTML message to the configured Telegram chat
   */
  public async sendMessage(htmlMessage: string): Promise<boolean> {
    if (!this.isEnabled()) {
      logger.debug('Telegram notification skipped (Telegram is disabled or credentials not set)');
      return false;
    }

    try {
      const url = `${this.baseUrl}/sendMessage`;
      await axios.post(
        url,
        {
          chat_id: this.chatId,
          text: htmlMessage,
          parse_mode: 'HTML',
          disable_web_page_preview: true,
        },
        { timeout: 8000 }
      );
      return true;
    } catch (err: any) {
      const errorMsg = err.response?.data?.description || err.message;
      logger.warn({ error: errorMsg }, 'Failed to deliver Telegram notification');
      return false;
    }
  }

  /**
   * Verify Telegram Bot Token and Chat ID connection
   */
  public async verifyConnection(): Promise<{ success: boolean; botName?: string; error?: string }> {
    if (!this.botToken) {
      return { success: false, error: 'TELEGRAM_BOT_TOKEN is not configured.' };
    }
    if (!this.chatId) {
      return { success: false, error: 'TELEGRAM_CHAT_ID is not configured.' };
    }

    try {
      const resp = await axios.get(`${this.baseUrl}/getMe`, { timeout: 8000 });
      const botInfo = resp.data?.result;
      return {
        success: true,
        botName: botInfo?.username || botInfo?.first_name || 'Bot',
      };
    } catch (err: any) {
      const errorMsg = err.response?.data?.description || err.message;
      return {
        success: false,
        error: `Telegram API check failed: ${errorMsg}`,
      };
    }
  }

  /**
   * Notify: New 4H Supertrend Signal & 50% Initial Position Entry
   */
  public async notifyTrendSignal(params: TrendSignalParams): Promise<void> {
    const isLong = params.direction === 'BULLISH';
    const arrow = isLong ? '🟢 LONG' : '🔴 SHORT';
    const formattedPrice = params.price.toFixed(2);

    const message = [
      `🚀 <b>NEW SUPERTREND SIGNAL: ${arrow}</b>`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `🪙 <b>Primary:</b> <code>${params.symbol}</code>`,
      `📊 <b>Timeframe:</b> 4-Hour Completed Candle`,
      `📦 <b>Initial Entry:</b> ${params.initialLots} lots (50% entry)`,
      `💵 <b>Entry Price:</b> $${formattedPrice}`,
      `⚡ <b>Leverage:</b> ${params.leverage}x`,
      `📋 <b>Adverse Grids:</b> ${params.gridLevels} limit orders placed (${params.gridLotsPerLevel} lots @ ${params.spacingPercent}% intervals)`,
      `⏱ <b>Time:</b> ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC`,
    ].join('\n');

    await this.sendMessage(message);
  }

  /**
   * Notify: Order Filled (Primary initial or Grid level)
   */
  public async notifyOrderFilled(params: OrderFilledParams): Promise<void> {
    const sideEmoji = params.side === 'buy' ? '🟢 BUY' : '🔴 SELL';
    const isGrid = params.purpose === 'GRID_ENTRY';
    const title = isGrid ? `📊 <b>GRID LEVEL ${params.level || ''} FILLED</b>` : `📦 <b>ORDER FILLED: ${params.purpose}</b>`;

    const lines = [
      title,
      `━━━━━━━━━━━━━━━━━━━━`,
      `🪙 <b>Instrument:</b> <code>${params.symbol}</code>`,
      `⚡ <b>Action:</b> ${sideEmoji} ${params.lots} lots`,
      `💵 <b>Fill Price:</b> $${params.price.toFixed(2)}`,
    ];

    if (params.avgPrice) {
      lines.push(`📈 <b>New Avg Price:</b> $${params.avgPrice.toFixed(2)}`);
    }
    if (params.totalLots) {
      lines.push(`💼 <b>Total Primary Lots:</b> ${params.totalLots} contracts`);
    }

    lines.push(`⏱ <b>Time:</b> ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC`);

    await this.sendMessage(lines.join('\n'));
  }

  /**
   * Notify: Opposite Hedge Position Opened (PAXGUSD)
   */
  public async notifyHedgeOpened(params: HedgeOpenedParams): Promise<void> {
    const hedgeSide = params.side === 'buy' ? '🟢 LONG' : '🔴 SHORT';
    const message = [
      `🛡️ <b>HEDGE POSITION OPENED</b>`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `🪙 <b>Instrument:</b> <code>${params.symbol}</code>`,
      `🎯 <b>Trigger:</b> Level ${params.level} (+${(params.level * params.triggerPct).toFixed(1)}% favorable move on primary)`,
      `🔄 <b>Hedge Side:</b> ${hedgeSide}`,
      `📦 <b>Hedge Size:</b> ${params.lots} lots`,
      `💵 <b>Hedge Entry:</b> $${params.entryPrice.toFixed(2)}`,
      `🎯 <b>Take-Profit Target:</b> $${params.targetPrice.toFixed(2)} (1.0% target)`,
      `📈 <b>Primary Price:</b> $${params.primaryPrice.toFixed(2)}`,
      `⏱ <b>Time:</b> ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC`,
    ].join('\n');

    await this.sendMessage(message);
  }

  /**
   * Notify: Hedge Take-Profit Hit & Profit Booked
   */
  public async notifyHedgeTakeProfit(params: HedgeTakeProfitParams): Promise<void> {
    const pnlFormatted = params.realizedPnl >= 0 ? `+$${params.realizedPnl.toFixed(2)}` : `-$${Math.abs(params.realizedPnl).toFixed(2)}`;
    const message = [
      `💰 <b>HEDGE TAKE-PROFIT ACHIEVED</b>`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `🪙 <b>Instrument:</b> <code>${params.symbol}</code>`,
      `🎯 <b>Hedge Level:</b> Level ${params.level}`,
      `💵 <b>Exit Price:</b> $${params.exitPrice.toFixed(2)}`,
      `💵 <b>Realized P&L:</b> <code>${pnlFormatted} USD</code>`,
      `💳 <b>Trading Fee:</b> $${params.fees.toFixed(4)} USD`,
      `🛡️ <b>Primary Position:</b> 100% PRESERVED & ACTIVE`,
    ];

    if (typeof params.remainingPrimaryLots === 'number') {
      message.push(`💼 <b>Primary Position Lots:</b> ${params.remainingPrimaryLots} contracts`);
    }

    message.push(`⏱ <b>Time:</b> ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC`);

    await this.sendMessage(message.join('\n'));
  }

  /**
   * Notify: Trend Reversal Sequence & Rebalancing
   */
  public async notifyTrendReversal(params: TrendReversalParams): Promise<void> {
    const message = [
      `🔄 <b>TREND REVERSAL DETECTED</b>`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `🔁 <b>Direction Change:</b> <code>${params.fromDirection}</code> ➡️ <code>${params.toDirection}</code>`,
      `🚫 <b>Grid Orders:</b> Cancelled all pending limit orders`,
      `🛡️ <b>Active Hedges:</b> Closed ${params.hedgesClosedCount} open hedge positions`,
      `💼 <b>Previous Primary:</b> Closed ${params.primaryLotsClosed} lots`,
      `🏁 <b>Status:</b> Preparing new cycle in opposite direction`,
      `⏱ <b>Time:</b> ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC`,
    ].join('\n');

    await this.sendMessage(message);
  }

  /**
   * Notify: Emergency Flatten Executed
   */
  public async notifyEmergencyFlatten(params: EmergencyFlattenParams): Promise<void> {
    const positionLines = params.closedPositions.length > 0
      ? params.closedPositions.map((p) => `  • <code>${p.symbol}</code>: ${p.size} contracts closed`).join('\n')
      : '  • No open positions to close';

    const message = [
      `🚨 <b>EMERGENCY FLATTEN EXECUTED</b>`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `⚠️ <b>Reason:</b> ${params.reason}`,
      `🛑 <b>Status:</b> Strategy set to <code>${params.status}</code>`,
      `❌ <b>Orders Cancelled:</b> ${params.cancelledOrdersCount} pending orders`,
      `📉 <b>Positions Flattened:</b>\n${positionLines}`,
      `🛡️ <b>Net Exposure:</b> 0 lots`,
      `⏱ <b>Time:</b> ${new Date().toISOString().replace('T', ' ').substring(0, 19)} UTC`,
    ].join('\n');

    await this.sendMessage(message);
  }

  /**
   * Handle generic system alerts from AlertService
   */
  private handleSystemAlert(alert: AlertEvent): void {
    if (!this.isEnabled()) return;

    // Filter: CRITICAL always sent, WARNING always sent, INFO sent if trade or risk related
    if (alert.severity === 'INFO' && !alert.title.includes('Trade') && !alert.title.includes('Hedge') && !alert.title.includes('Trend') && !alert.title.includes('Started') && !alert.title.includes('Stopped')) {
      return;
    }

    const icon = alert.severity === 'CRITICAL' ? '🚨' : alert.severity === 'WARNING' ? '⚠️' : 'ℹ️';
    const message = [
      `${icon} <b>[${alert.severity}] ${alert.title}</b>`,
      `━━━━━━━━━━━━━━━━━━━━`,
      `${alert.message}`,
      `⏱ <b>Time:</b> ${new Date(alert.timestamp).toISOString().replace('T', ' ').substring(0, 19)} UTC`,
    ].join('\n');

    this.sendMessage(message).catch((err) => {
      logger.debug({ err: err.message }, 'Failed to send alert to Telegram');
    });
  }
}

export const telegram = new TelegramService();
