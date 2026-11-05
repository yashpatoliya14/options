/**
 * Configuration module for Options Arbitrage Bot.
 * Delta Exchange (Long) vs CoinSwitch PRO (Short)
 */

import path from "path";
import dotenv from "dotenv";

dotenv.config({ path: path.resolve(__dirname, "../.env") });

export interface AssetConfig {
  name: string;
  symbol: string;
  deltaUnderlying: string;     // Delta Exchange underlying symbol
  coinswitchPrefix: string;    // CoinSwitch instrument prefix
  lotSizeUnits: number;        // Base units per arbitrage trade (0.1 for all)
  coinswitchLots: number;      // CoinSwitch lots for 0.1 units (1 lot = 0.1)
  deltaLots: number;           // Delta lots for 0.1 units (10 lots = 0.1)
  deltaContractSize: number;   // Size of 1 Delta contract in base units (0.01)
}

export const ASSETS: Record<string, AssetConfig> = {
  BTC: {
    name: "Bitcoin",
    symbol: "BTC",
    deltaUnderlying: "BTC",
    coinswitchPrefix: "BTC",
    lotSizeUnits: 0.1,          // 1 trade = 0.1 BTC
    coinswitchLots: 1,          // 1 CoinSwitch lot = 0.1 BTC
    deltaLots: 10,              // 10 Delta lots = 0.1 BTC
    deltaContractSize: 0.01,    // 1 Delta contract = 0.01 BTC
  },
  ETH: {
    name: "Ethereum",
    symbol: "ETH",
    deltaUnderlying: "ETH",
    coinswitchPrefix: "ETH",
    lotSizeUnits: 0.1,
    coinswitchLots: 1,
    deltaLots: 10,
    deltaContractSize: 0.01,
  },
  XAUTUSDT: {
    name: "Tether Gold",
    symbol: "XAUTUSDT",
    deltaUnderlying: "XAUT",
    coinswitchPrefix: "XAUT",
    lotSizeUnits: 0.1,
    coinswitchLots: 1,
    deltaLots: 10,
    deltaContractSize: 0.01,
  },
};

export const Config = {
  // Delta Exchange
  DELTA_API_KEY: (process.env.DELTA_API_KEY || "").trim(),
  DELTA_API_SECRET: (process.env.DELTA_API_SECRET || "").trim(),
  USE_TESTNET: (process.env.USE_TESTNET || "false").toLowerCase() === "true",
  DELTA_TESTNET_URL: process.env.DELTA_TESTNET_URL || "https://cdn-ind.testnet.deltaex.org",
  DELTA_PROD_URL: process.env.DELTA_BASE_URL || "https://api.india.delta.exchange",

  getDeltaBaseUrl(): string {
    return this.USE_TESTNET ? this.DELTA_TESTNET_URL : this.DELTA_PROD_URL;
  },

  // CoinSwitch PRO
  COINSWITCH_API_KEY: (process.env.COINSWITCH_API_KEY || "").trim(),
  COINSWITCH_SECRET_KEY: (process.env.COINSWITCH_SECRET_KEY || "").trim(),
  COINSWITCH_BASE_URL: process.env.COINSWITCH_BASE_URL || "https://coinswitch.co",
  COINSWITCH_PRO_URL: process.env.COINSWITCH_PRO_URL || "https://api-trading.coinswitch.co",

  // Telegram
  TELEGRAM_ENABLED: (process.env.TELEGRAM_ENABLED || "true").toLowerCase() === "true",
  TELEGRAM_BOT_TOKEN: (process.env.TELEGRAM_BOT_TOKEN || "").trim(),
  TELEGRAM_CHAT_ID: (process.env.TELEGRAM_CHAT_ID || "").trim(),

  // Pricing & Currency
  USD_INR_RATE: parseFloat(process.env.USD_INR_RATE || "86.50"),
  NUM_OTM_STRIKES: parseInt(process.env.NUM_OTM_STRIKES || "5", 10),

  // Fees & Arbitrage Filtering
  // Delta Exchange options taker fee: ~0.03% - 0.05% of notional, capped at 10% of premium
  DELTA_FEE_PCT: parseFloat(process.env.DELTA_FEE_PCT || "0.05"),
  // CoinSwitch PRO options taker fee: ~0.05% of notional, capped at 10% of premium
  COINSWITCH_FEE_PCT: parseFloat(process.env.COINSWITCH_FEE_PCT || "0.05"),
  // Max cap on fee as percentage of option premium (10%)
  MAX_PREMIUM_FEE_CAP_PCT: parseFloat(process.env.MAX_PREMIUM_FEE_CAP_PCT || "10.0"),

  // Auto close time (1:30 PM IST = 08:00 UTC)
  AUTO_CLOSE_HOUR_IST: 13,
  AUTO_CLOSE_MINUTE_IST: 30,
};
