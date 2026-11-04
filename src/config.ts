/**
 * Configuration module for Options Arbitrage Bot.
 * Delta Exchange (Long) vs CoinDCX (Short)
 */

import path from "path";
import dotenv from "dotenv";

dotenv.config({ path: path.resolve(__dirname, "../.env") });

export interface AssetConfig {
  name: string;
  symbol: string;
  deltaUnderlying: string;   // Delta Exchange underlying symbol
  coindcxPrefix: string;     // CoinDCX instrument prefix
  lotSizeUnits: number;      // Base units per "lot" (0.1 for all)
  coindcxLots: number;       // CoinDCX lots for 0.1 units
  deltaLots: number;         // Delta lots for 0.1 units
  deltaContractSize: number; // Size of 1 Delta contract in base units
}

export const ASSETS: Record<string, AssetConfig> = {
  BTC: {
    name: "Bitcoin",
    symbol: "BTC",
    deltaUnderlying: "BTC",
    coindcxPrefix: "BTC",
    lotSizeUnits: 0.1,        // 1 lot = 0.1 BTC on CoinDCX
    coindcxLots: 1,           // 1 CoinDCX lot = 0.1 BTC
    deltaLots: 10,            // 10 Delta lots = 0.1 BTC
    deltaContractSize: 0.01,  // 1 Delta contract = 0.01 BTC
  },
  ETH: {
    name: "Ethereum",
    symbol: "ETH",
    deltaUnderlying: "ETH",
    coindcxPrefix: "ETH",
    lotSizeUnits: 0.1,
    coindcxLots: 1,
    deltaLots: 10,
    deltaContractSize: 0.01,
  },
  XAUTUSDT: {
    name: "Tether Gold",
    symbol: "XAUTUSDT",
    deltaUnderlying: "XAUT",
    coindcxPrefix: "XAUT",
    lotSizeUnits: 0.1,
    coindcxLots: 1,
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

  // CoinDCX
  COINDCX_API_KEY: (process.env.COINDCX_API_KEY || "").trim(),
  COINDCX_API_SECRET: (process.env.COINDCX_API_SECRET || "").trim(),
  COINDCX_BASE_URL: process.env.COINDCX_BASE_URL || "https://api.coindcx.com",

  // Telegram
  TELEGRAM_ENABLED: (process.env.TELEGRAM_ENABLED || "true").toLowerCase() === "true",
  TELEGRAM_BOT_TOKEN: (process.env.TELEGRAM_BOT_TOKEN || "").trim(),
  TELEGRAM_CHAT_ID: (process.env.TELEGRAM_CHAT_ID || "").trim(),

  // Pricing
  USD_INR_RATE: parseFloat(process.env.USD_INR_RATE || "86.50"),
  NUM_OTM_STRIKES: parseInt(process.env.NUM_OTM_STRIKES || "5", 10),

  // Auto close time (1:30 PM IST)
  AUTO_CLOSE_HOUR_IST: 13,
  AUTO_CLOSE_MINUTE_IST: 30,
};
