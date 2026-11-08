/**
 * Dedicated Delta Exchange India REST API Client
 */

import axios, { AxiosInstance } from 'axios';
import { env } from '../config/env';
import { DeltaAuth } from './delta.auth';
import { logger } from '../monitoring/logger';
import {
  DeltaProduct,
  DeltaTicker,
  DeltaCandle,
  DeltaOrderRequest,
  DeltaOrderResponse,
  DeltaPosition,
  DeltaWalletBalance,
} from '../types/exchange.types';

export class DeltaExchangeClient {
  private apiKey: string;
  private apiSecret: string;
  private baseUrl: string;
  private http: AxiosInstance;

  constructor(apiKey?: string, apiSecret?: string, baseUrl?: string) {
    this.apiKey = apiKey || env.DELTA_API_KEY;
    this.apiSecret = apiSecret || env.DELTA_API_SECRET;
    this.baseUrl = (baseUrl || env.DELTA_BASE_URL).replace(/\/+$/, '');

    this.http = axios.create({
      baseURL: this.baseUrl,
      timeout: 10000,
      headers: {
        'User-Agent': 'DeltaGoldAlgo/1.0',
        'Content-Type': 'application/json',
      },
    });

    // Automatically sync time offset with Delta Exchange server
    this.http.interceptors.response.use(
      (response) => {
        if (response.headers['date']) {
          const serverTimeMs = new Date(response.headers['date']).getTime();
          DeltaAuth.setServerTimeOffset(serverTimeMs - Date.now());
        }
        return response;
      },
      (error) => {
        if (error.response?.headers?.['date']) {
          const serverTimeMs = new Date(error.response.headers['date']).getTime();
          DeltaAuth.setServerTimeOffset(serverTimeMs - Date.now());
        }
        return Promise.reject(error);
      }
    );
  }

  /**
   * Fetch all trading products
   */
  public async getProducts(): Promise<DeltaProduct[]> {
    try {
      const resp = await this.http.get('/v2/products');
      return resp.data?.result || [];
    } catch (err: any) {
      logger.error({ error: err.message }, 'Failed to fetch Delta products');
      throw err;
    }
  }

  /**
   * Fetch tickers for all products (or filtered by symbol)
   */
  public async getTickers(): Promise<DeltaTicker[]> {
    try {
      const resp = await this.http.get('/v2/tickers');
      return resp.data?.result || [];
    } catch (err: any) {
      logger.error({ error: err.message }, 'Failed to fetch Delta tickers');
      throw err;
    }
  }

  /**
   * Fetch historical candles for an instrument
   */
  public async getHistoricalCandles(
    symbol: string,
    resolution: string = '4h',
    startTimeSec: number,
    endTimeSec: number
  ): Promise<DeltaCandle[]> {
    const path = '/v2/history/candles';
    const qs = `resolution=${resolution}&symbol=${symbol}&start=${startTimeSec}&end=${endTimeSec}`;

    try {
      const resp = await this.http.get(`${path}?${qs}`);
      const rawCandles: any[] = resp.data?.result || [];

      // Sort chronological (oldest to newest)
      return rawCandles
        .map((c) => ({
          time: c.time,
          open: parseFloat(c.open),
          high: parseFloat(c.high),
          low: parseFloat(c.low),
          close: parseFloat(c.close),
          volume: parseFloat(c.volume || '0'),
        }))
        .sort((a, b) => a.time - b.time);
    } catch (err: any) {
      logger.error({ error: err.message, symbol, resolution }, 'Failed to fetch Delta candles');
      throw err;
    }
  }

  public async getCandles(
    symbol: string,
    resolution: string = '4h',
    startTimeSec: number,
    endTimeSec: number
  ): Promise<DeltaCandle[]> {
    return this.getHistoricalCandles(symbol, resolution, startTimeSec, endTimeSec);
  }

  /**
   * Fetch account wallet balances (USDT / USD / INR)
   */
  public async getAccountBalance(): Promise<DeltaWalletBalance[]> {
    const path = '/v2/wallet/balances';
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'GET', path);

    try {
      const resp = await this.http.get(path, { headers });
      return resp.data?.result || [];
    } catch (err: any) {
      logger.error({ error: err.message }, 'Failed to fetch Delta wallet balances');
      throw err;
    }
  }

  /**
   * Fetch open margined positions
   */
  public async getOpenPositions(): Promise<DeltaPosition[]> {
    const path = '/v2/positions/margined';
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'GET', path);

    try {
      const resp = await this.http.get(path, { headers });
      return resp.data?.result || [];
    } catch (err: any) {
      logger.error({ error: err.message }, 'Failed to fetch Delta positions');
      throw err;
    }
  }

  public async getPositions(): Promise<DeltaPosition[]> {
    return this.getOpenPositions();
  }

  /**
   * Fetch open working orders
   */
  public async getOpenOrders(productId?: number): Promise<DeltaOrderResponse[]> {
    const path = '/v2/orders';
    const qs = productId ? `product_id=${productId}&state=open` : 'state=open';
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'GET', path, undefined, qs);

    try {
      const resp = await this.http.get(`${path}?${qs}`, { headers });
      return resp.data?.result || [];
    } catch (err: any) {
      logger.error({ error: err.message }, 'Failed to fetch Delta open orders');
      throw err;
    }
  }

  /**
   * Place limit or market order
   */
  public async placeOrder(order: DeltaOrderRequest): Promise<DeltaOrderResponse> {
    const path = '/v2/orders';
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'POST', path, order);

    try {
      const resp = await this.http.post(path, order, { headers });
      return resp.data?.result;
    } catch (err: any) {
      const errorData = err.response?.data?.error;
      const msg = (errorData && typeof errorData === 'object' ? `${errorData.code || ''}: ${errorData.message || JSON.stringify(errorData)}` : null)
        || err.response?.data?.message
        || err.message;
      logger.error({ error: msg, order }, 'Delta placeOrder rejected');
      throw new Error(`Delta placeOrder failed: ${msg}`);
    }
  }

  /**
   * Cancel an order
   */
  public async cancelOrder(arg1: number | string, arg2?: number | string): Promise<boolean> {
    const orderId = typeof arg2 !== 'undefined' ? arg2 : arg1;
    const productId = typeof arg2 !== 'undefined' ? arg1 : undefined;

    const path = '/v2/orders';
    const body: any = { id: Number(orderId) };
    if (productId) body.product_id = Number(productId);

    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'DELETE', path, body);

    try {
      await this.http.delete(path, { data: body, headers });
      return true;
    } catch (err: any) {
      logger.warn({ error: err.message, orderId }, 'Delta cancelOrder failed');
      return false;
    }
  }

  /**
   * Cancel all open orders for a specific product (or all products)
   */
  public async cancelAllOrders(productId?: number): Promise<number> {
    let cancelledCount = 0;

    // 1. Fetch current open orders to track and cancel explicitly
    try {
      const openOrders = await this.getOpenOrders(productId);
      for (const order of openOrders) {
        const ok = await this.cancelOrder(order.product_id, order.id);
        if (ok) cancelledCount++;
      }
    } catch (err: any) {
      logger.warn({ error: err.message }, 'Failed to fetch open orders for individual cancellation');
    }

    // 2. Also send bulk cancel endpoint if available on Delta
    const path = '/v2/orders/all';
    const body: any = {};
    if (productId) body.product_id = Number(productId);
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'DELETE', path, body);

    try {
      await this.http.delete(path, { data: body, headers });
    } catch {
      // Bulk cancellation endpoint may not be implemented for all products, individual loop already executed
    }

    return cancelledCount;
  }

  /**
   * Immediately close an open position with a market order using reduce_only: true
   */
  public async closePositionMarket(productId: number, size: number): Promise<DeltaOrderResponse | null> {
    if (!size || size === 0) return null;

    const absSize = Math.abs(size);
    const side: 'buy' | 'sell' = size > 0 ? 'sell' : 'buy';

    logger.info(
      { productId, size, closingSide: side, contracts: absSize },
      'Submitting market reduce_only order to flatten position on Delta'
    );

    return this.placeOrder({
      product_id: productId,
      size: absSize,
      side,
      order_type: 'market_order',
      reduce_only: true,
    });
  }

  /**
   * Get specific order status
   */
  public async getOrderStatus(arg1: number | string, arg2?: string | number): Promise<DeltaOrderResponse> {
    const orderId = typeof arg2 !== 'undefined' ? arg2 : arg1;
    const path = `/v2/orders/${orderId}`;
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'GET', path);

    try {
      const resp = await this.http.get(path, { headers });
      return resp.data?.result;
    } catch (err: any) {
      logger.error({ error: err.message, orderId }, 'Failed to get Delta order status');
      throw err;
    }
  }

  /**
   * Set leverage for a contract product
   */
  public async setLeverage(productId: number, leverage: number): Promise<boolean> {
    const path = '/v2/products/orders/leverage';
    const body = { product_id: productId, leverage: leverage.toString() };
    const headers = DeltaAuth.sign(this.apiKey, this.apiSecret, 'POST', path, body);

    try {
      await this.http.post(path, body, { headers });
      logger.info({ productId, leverage }, 'Updated product leverage on Delta');
      return true;
    } catch (err: any) {
      logger.warn({ error: err.message, productId, leverage }, 'Failed to set product leverage on Delta');
      return false;
    }
  }
}

export { DeltaExchangeClient as DeltaClient };
