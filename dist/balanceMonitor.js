"use strict";
/**
 * Balance Monitor for Live Delta Exchange and CoinDCX.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.BalanceMonitor = void 0;
const config_1 = require("./config");
const deltaClient_1 = require("./deltaClient");
const coindcxClient_1 = require("./coindcxClient");
class BalanceMonitor {
    deltaClient;
    coindcxClient;
    constructor(deltaClient, coindcxClient) {
        this.deltaClient = deltaClient || new deltaClient_1.DeltaClient();
        this.coindcxClient = coindcxClient || new coindcxClient_1.CoinDCXClient();
    }
    async checkBalances() {
        const [delta, coindcx] = await Promise.all([
            this.deltaClient.getWalletBalance(),
            this.coindcxClient.getWalletBalance(),
        ]);
        const totalBalanceUsd = Math.round((delta.balanceUsd + coindcx.balanceUsd) * 100) / 100;
        const totalBalanceInr = Math.round(totalBalanceUsd * config_1.Config.USD_INR_RATE * 100) / 100;
        const totalAvailableUsd = Math.round((delta.availableUsd + coindcx.availableUsd) * 100) / 100;
        const totalAvailableInr = Math.round(totalAvailableUsd * config_1.Config.USD_INR_RATE * 100) / 100;
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
exports.BalanceMonitor = BalanceMonitor;
