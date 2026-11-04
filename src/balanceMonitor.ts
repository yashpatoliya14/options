/**
 * Balance Monitor for Delta Exchange and CoinDCX.
 */

import { Config, ASSETS } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinDCXClient } from "./coindcxClient";

export interface BalanceResult {
  deltaUsd: number;
  deltaInr: number;
  coindcxUsd: number;
  coindcxInr: number;
  totalUsd: number;
  totalInr: number;
  deltaError?: string;
  coindcxError?: string;
}

export class BalanceMonitor {
  public deltaClient: DeltaClient;
  public coindcxClient: CoinDCXClient;

  constructor(deltaClient?: DeltaClient, coindcxClient?: CoinDCXClient) {
    this.deltaClient = deltaClient || new DeltaClient();
    this.coindcxClient = coindcxClient || new CoinDCXClient();
  }

  public async checkBalances(): Promise<BalanceResult> {
    // Delta balance
    let deltaUsd = 0;
    let deltaError: string | undefined;
    try {
      // We'd need to implement wallet balance for Delta
      // For now, using positions-based estimation
      deltaUsd = 0;
    } catch (err: any) {
      deltaError = err.message;
    }

    // CoinDCX balance
    let coindcxUsd = 0;
    let coindcxError: string | undefined;
    try {
      coindcxUsd = 0;
    } catch (err: any) {
      coindcxError = err.message;
    }

    const totalUsd = deltaUsd + coindcxUsd;

    return {
      deltaUsd,
      deltaInr: Math.round(deltaUsd * Config.USD_INR_RATE * 100) / 100,
      coindcxUsd,
      coindcxInr: Math.round(coindcxUsd * Config.USD_INR_RATE * 100) / 100,
      totalUsd,
      totalInr: Math.round(totalUsd * Config.USD_INR_RATE * 100) / 100,
      deltaError,
      coindcxError,
    };
  }
}
