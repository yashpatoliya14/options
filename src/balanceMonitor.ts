/**
 * Balance Monitor for Live Delta Exchange and CoinDCX.
 */

import { Config } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinDCXClient } from "./coindcxClient";
import { CombinedWalletReport } from "./types";

export class BalanceMonitor {
  public deltaClient: DeltaClient;
  public coindcxClient: CoinDCXClient;

  constructor(deltaClient?: DeltaClient, coindcxClient?: CoinDCXClient) {
    this.deltaClient = deltaClient || new DeltaClient();
    this.coindcxClient = coindcxClient || new CoinDCXClient();
  }

  public async checkBalances(): Promise<CombinedWalletReport> {
    const [delta, coindcx] = await Promise.all([
      this.deltaClient.getWalletBalance(),
      this.coindcxClient.getWalletBalance(),
    ]);

    const totalBalanceUsd = Math.round((delta.balanceUsd + coindcx.balanceUsd) * 100) / 100;
    const totalBalanceInr = Math.round(totalBalanceUsd * Config.USD_INR_RATE * 100) / 100;
    const totalAvailableUsd = Math.round((delta.availableUsd + coindcx.availableUsd) * 100) / 100;
    const totalAvailableInr = Math.round(totalAvailableUsd * Config.USD_INR_RATE * 100) / 100;

    return {
      timestamp: new Date(),
      delta,
      coindcx,
      totalBalanceUsd,
      totalBalanceInr,
      totalAvailableUsd,
      totalAvailableInr,
    };
  }
}
