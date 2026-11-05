"use strict";
/**
 * Balance Monitor for Live Delta Exchange and CoinSwitch PRO.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.BalanceMonitor = void 0;
const config_1 = require("./config");
const deltaClient_1 = require("./deltaClient");
const coinSwitchClient_1 = require("./coinSwitchClient");
class BalanceMonitor {
    deltaClient;
    coinswitchClient;
    constructor(deltaClient, coinswitchClient) {
        this.deltaClient = deltaClient || new deltaClient_1.DeltaClient();
        this.coinswitchClient = coinswitchClient || new coinSwitchClient_1.CoinSwitchClient();
    }
    async checkBalances() {
        const [delta, coinswitch] = await Promise.all([
            this.deltaClient.getWalletBalance(),
            this.coinswitchClient.getWalletBalance(),
        ]);
        const totalBalanceUsd = Math.round((delta.balanceUsd + coinswitch.balanceUsd) * 100) / 100;
        const totalBalanceInr = Math.round(totalBalanceUsd * config_1.Config.USD_INR_RATE * 100) / 100;
        const totalAvailableUsd = Math.round((delta.availableUsd + coinswitch.availableUsd) * 100) / 100;
        const totalAvailableInr = Math.round(totalAvailableUsd * config_1.Config.USD_INR_RATE * 100) / 100;
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
exports.BalanceMonitor = BalanceMonitor;
