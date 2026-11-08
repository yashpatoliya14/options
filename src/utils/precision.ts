/**
 * Financial Precision and Arithmetic Utilities using Decimal.js
 */

import Decimal from 'decimal.js';

// Set high precision for financial safety
Decimal.set({ precision: 28, rounding: Decimal.ROUND_HALF_UP });

export class Precision {
  /**
   * Convert number or string to Decimal
   */
  public static d(value: number | string | Decimal): Decimal {
    return new Decimal(value);
  }

  /**
   * Round price to exchange tick size (e.g. 0.01)
   */
  public static roundToTickSize(price: number | string | Decimal, tickSize: number | string | Decimal): number {
    const p = new Decimal(price);
    const tick = new Decimal(tickSize);
    if (tick.isZero()) return p.toNumber();
    return p.dividedBy(tick).round().times(tick).toNumber();
  }

  /**
   * Calculate contract quantity from planned USD notional:
   * Notional = contracts * contract_value * price
   * => contracts = Math.floor(Notional / (contract_value * price))
   * (contracts must be an integer >= 1)
   */
  public static calculateContractsFromNotional(
    notionalUsd: number | string | Decimal,
    price: number | string | Decimal,
    contractValue: number | string | Decimal = '0.001'
  ): number {
    const notional = new Decimal(notionalUsd);
    const p = new Decimal(price);
    const cv = new Decimal(contractValue);

    if (p.isZero() || cv.isZero()) return 0;
    const valuePerContract = p.times(cv);
    const contracts = notional.dividedBy(valuePerContract).floor();
    return Math.max(1, contracts.toNumber());
  }

  /**
   * Calculate exact USD notional for a contract position:
   * Notional = contracts * contract_value * price
   */
  public static calculateNotional(
    contracts: number | string | Decimal,
    price: number | string | Decimal,
    contractValue: number | string | Decimal = '0.001'
  ): number {
    const c = new Decimal(contracts);
    const p = new Decimal(price);
    const cv = new Decimal(contractValue);
    return c.times(cv).times(p).toNumber();
  }

  /**
   * Calculate Unrealized PnL:
   * LONG: contracts * contract_value * (markPrice - entryPrice)
   * SHORT: contracts * contract_value * (entryPrice - markPrice)
   */
  public static calculatePnl(
    side: 'LONG' | 'SHORT' | 'buy' | 'sell',
    entryPrice: number | string | Decimal,
    exitPrice: number | string | Decimal,
    contracts: number | string | Decimal,
    contractValue: number | string | Decimal = '0.001'
  ): number {
    const entry = new Decimal(entryPrice);
    const exit = new Decimal(exitPrice);
    const c = new Decimal(contracts);
    const cv = new Decimal(contractValue);

    const isLong = side.toUpperCase() === 'LONG' || side.toLowerCase() === 'buy';
    const priceDiff = isLong ? exit.minus(entry) : entry.minus(exit);
    return priceDiff.times(cv).times(c).toNumber();
  }

  /**
   * Round monetary values to 2 decimal places (cents / USD)
   */
  public static roundCurrency(value: number | string | Decimal): number {
    return new Decimal(value).toDecimalPlaces(2, Decimal.ROUND_HALF_UP).toNumber();
  }

  /**
   * Format decimal to string with exact decimal places
   */
  public static formatFixed(value: number | string | Decimal, decimals: number = 2): string {
    return new Decimal(value).toFixed(decimals);
  }
}

