/**
 * Product Discovery & Specification Service
 */

import { DeltaExchangeClient } from './delta.client';
import { DeltaProduct } from '../types/exchange.types';
import { logger } from '../monitoring/logger';

export class ProductsService {
  private client: DeltaExchangeClient;
  private productsCache: Map<string, DeltaProduct> = new Map();
  private lastFetched: number = 0;
  private readonly CACHE_TTL_MS = 3600 * 1000; // 1 hour

  constructor(client?: DeltaExchangeClient) {
    this.client = client || new DeltaExchangeClient();
  }

  /**
   * Load and cache all products from Delta Exchange
   */
  public async loadProducts(): Promise<void> {
    if (Date.now() - this.lastFetched < this.CACHE_TTL_MS && this.productsCache.size > 0) {
      return;
    }

    logger.info('Fetching product directory from Delta Exchange India...');
    const products = await this.client.getProducts();

    this.productsCache.clear();
    for (const p of products) {
      if (p.symbol) {
        this.productsCache.set(p.symbol.toUpperCase(), p);
      }
    }

    this.lastFetched = Date.now();
    logger.info({ totalProducts: products.length }, 'Cached Delta Exchange products');
  }

  public getProduct(symbol: string): DeltaProduct | null {
    return this.productsCache.get(symbol.toUpperCase()) || null;
  }

  /**
   * Dynamically verify that both primary and hedge gold products exist on Delta Exchange India.
   * Requirement from Section 2:
   * "Discover actual supported products dynamically using the exchange API.
   *  If two suitable gold instruments are unavailable, clearly explain the limitation
   *  and do not execute the original paired-hedging strategy using unrelated products."
   */
  public async verifyGoldProducts(
    primarySymbol: string = 'XAUTUSD',
    hedgeSymbol: string = 'PAXGUSD'
  ): Promise<{ primary: DeltaProduct; hedge: DeltaProduct }> {
    await this.loadProducts();

    const primary = this.getProduct(primarySymbol);
    const hedge = this.getProduct(hedgeSymbol);

    if (!primary) {
      throw new Error(
        `[CRITICAL PRODUCT ERROR] Primary gold instrument '${primarySymbol}' is not available on Delta Exchange India. Cannot execute Gold Trend strategy.`
      );
    }

    if (!hedge) {
      throw new Error(
        `[CRITICAL PRODUCT ERROR] Secondary hedge gold instrument '${hedgeSymbol}' is not available on Delta Exchange India. Cannot execute paired-hedging strategy without a valid correlated gold product.`
      );
    }

    if (primary.contract_type !== 'perpetual_futures' || hedge.contract_type !== 'perpetual_futures') {
      throw new Error(
        `[CRITICAL PRODUCT ERROR] Gold instruments must be perpetual futures. Found: ${primary.symbol} (${primary.contract_type}), ${hedge.symbol} (${hedge.contract_type}).`
      );
    }

    if (primary.state !== 'live' || hedge.state !== 'live') {
      throw new Error(
        `[CRITICAL PRODUCT ERROR] One or both gold instruments are not in 'live' state on Delta Exchange India.`
      );
    }

    logger.info(
      {
        primary: {
          id: primary.id,
          symbol: primary.symbol,
          contractValue: primary.contract_value,
          tickSize: primary.tick_size,
        },
        hedge: {
          id: hedge.id,
          symbol: hedge.symbol,
          contractValue: hedge.contract_value,
          tickSize: hedge.tick_size,
        },
      },
      '✅ Verified supported gold instruments on Delta Exchange India'
    );

    return { primary, hedge };
  }
}

