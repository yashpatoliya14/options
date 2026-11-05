/**
 * Balance Monitor for Live Delta Exchange and CoinSwitch PRO.
 */

import { Config } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinSwitchClient } from "./coinSwitchClient";
import { CombinedWalletReport } from "./types";

export class BalanceMonitor {
  public deltaClient: DeltaClient;
  public coinswitchClient: CoinSwitchClient;

  constructor(deltaClient?: DeltaClient, coinswitchClient?: CoinSwitchClient) {
    this.deltaClient = deltaClient || new DeltaClient();
    this.coinswitchClient = coinswitchClient || new CoinSwitchClient();
  }

  public async checkBalances(): Promise<CombinedWalletReport> {
    const [delta, coinswitch] = await Promise.all([
      this.deltaClient.getWalletBalance(),
      this.coinswitchClient.getWalletBalance(),
    ]);

    const totalBalanceUsd = Math.round((delta.balanceUsd + coinswitch.balanceUsd) * 100) / 100;
    const totalBalanceInr = Math.round(totalBalanceUsd * Config.USD_INR_RATE * 100) / 100;
    const totalAvailableUsd = Math.round((delta.availableUsd + coinswitch.availableUsd) * 100) / 100;
    const totalAvailableInr = Math.round(totalAvailableUsd * Config.USD_INR_RATE * 100) / 100;

    return {
      timestamp: new Date(),
      delta,
      coinswitch,
      totalBalanceUsd,
      totalBalanceInr,
      totalAvailableUsd,
      totalAvailableInr,
    };
  }
}
