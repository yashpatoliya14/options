/**
 * Delta Exchange India WebSocket Client
 * Real-time market data streaming, automatic reconnection, heartbeat, and subscription recovery.
 */

import WebSocket from 'ws';
import { EventEmitter } from 'events';
import { env } from '../config/env';
import { logger } from '../monitoring/logger';
import { DeltaTicker } from '../types/exchange.types';

export type TickerListener = (ticker: DeltaTicker) => void;
export type ReconnectListener = () => void;

export class DeltaWebSocketClient extends EventEmitter {
  private wsUrl: string;
  private ws: WebSocket | null = null;
  private isConnected = false;
  private isTerminated = false;
  private pingInterval: NodeJS.Timeout | null = null;
  private reconnectTimeout: NodeJS.Timeout | null = null;
  private reconnectAttempts = 0;
  private subscribedSymbols = new Set<string>();

  private tickerListeners: TickerListener[] = [];
  private reconnectListeners: ReconnectListener[] = [];

  constructor(wsUrl?: string) {
    super();
    this.wsUrl = wsUrl || env.DELTA_WS_URL;
  }

  public onTicker(listener: TickerListener): void {
    this.tickerListeners.push(listener);
    this.on('ticker', listener);
  }

  public onReconnect(listener: ReconnectListener): void {
    this.reconnectListeners.push(listener);
    this.on('reconnect', listener);
  }

  public subscribeTickers(symbols: string[]): void {
    for (const sym of symbols) {
      this.subscribedSymbols.add(sym);
    }

    if (this.isConnected && this.ws) {
      this.sendSubscription(symbols);
    }
  }

  public connect(): void {
    if (this.isTerminated) return;

    logger.info({ url: this.wsUrl }, 'Connecting to Delta WebSocket...');

    try {
      this.ws = new WebSocket(this.wsUrl);

      this.ws.on('open', () => {
        logger.info('✅ Delta WebSocket connected successfully.');
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.emit('open');

        // Restore subscriptions
        if (this.subscribedSymbols.size > 0) {
          this.sendSubscription(Array.from(this.subscribedSymbols));
        }

        this.startHeartbeat();

        // Notify reconnect listeners to trigger REST reconciliation
        this.emit('reconnect');
        for (const listener of this.reconnectListeners) {
          try {
            listener();
          } catch (err: any) {
            logger.error(`WebSocket reconnect listener error: ${err.message}`);
          }
        }
      });

      this.ws.on('message', (data: WebSocket.Data) => {
        this.handleMessage(data);
      });

      this.ws.on('close', (code, reason) => {
        logger.warn({ code, reason: reason.toString() }, 'Delta WebSocket connection closed');
        this.emit('close', code, reason);
        this.handleDisconnect();
      });

      this.ws.on('error', (err) => {
        logger.error({ error: err.message }, 'Delta WebSocket error');
        this.emit('error', err);
        this.handleDisconnect();
      });
    } catch (err: any) {
      logger.error({ error: err.message }, 'Delta WebSocket initial connection failure');
      this.emit('error', err);
      this.handleDisconnect();
    }
  }

  private sendSubscription(symbols: string[]): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    const payload = {
      type: 'subscribe',
      payload: {
        channels: [
          {
            name: 'v2/ticker',
            symbols,
          },
        ],
      },
    };

    this.ws.send(JSON.stringify(payload));
    logger.info({ symbols }, 'Subscribed to Delta ticker channel');
  }

  private handleMessage(data: WebSocket.Data): void {
    try {
      const msg = JSON.parse(data.toString());

      // Heartbeat reply
      if (msg.type === 'pong') return;

      // Ticker channel message
      if (msg.type === 'v2/ticker') {
        const ticker: DeltaTicker = {
          product_id: msg.product_id,
          symbol: msg.symbol,
          contract_type: msg.contract_type,
          mark_price: msg.mark_price,
          spot_price: msg.spot_price,
          close: parseFloat(msg.close || '0'),
          open: parseFloat(msg.open || '0'),
          high: parseFloat(msg.high || '0'),
          low: parseFloat(msg.low || '0'),
          volume: parseFloat(msg.volume || '0'),
          turnover: parseFloat(msg.turnover || '0'),
          timestamp: msg.timestamp || Date.now(),
          quotes: msg.quotes,
        };

        this.emit('ticker', ticker);

        for (const listener of this.tickerListeners) {
          try {
            listener(ticker);
          } catch (err: any) {
            logger.error(`Ticker listener error: ${err.message}`);
          }
        }
      }
    } catch (err: any) {
      logger.debug({ error: err.message }, 'Failed to parse WS message');
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.pingInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 20000);
  }

  private stopHeartbeat(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  private handleDisconnect(): void {
    this.isConnected = false;
    this.stopHeartbeat();

    if (this.ws) {
      try {
        this.ws.terminate();
      } catch {
        // ignore
      }
      this.ws = null;
    }

    if (this.isTerminated) return;

    // Exponential backoff reconnect
    this.reconnectAttempts++;
    const delay = Math.min(30000, 1000 * Math.pow(2, Math.min(this.reconnectAttempts, 5)));

    logger.info({ attempt: this.reconnectAttempts, delayMs: delay }, 'Scheduling WebSocket reconnection...');

    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);
    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, delay);
  }

  public disconnect(): void {
    this.isTerminated = true;
    this.stopHeartbeat();
    if (this.reconnectTimeout) clearTimeout(this.reconnectTimeout);

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.isConnected = false;
    logger.info('Delta WebSocket disconnected.');
  }
}
