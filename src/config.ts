/**
 * Configuration module for Options IV Arbitrage Algo.
 */

import path from "path";
import dotenv from "dotenv";

// Load .env
dotenv.config({ path: path.resolve(__dirname, "../.env") });

export const Config = {
  // Delta Exchange (Exchange 1: 5:30 PM IST / 12:00 UTC Expiry)
  DELTA_API_KEY: process.env.DELTA_API_KEY || "",
  DELTA_API_SECRET: process.env.DELTA_API_SECRET || "",
  USE_TESTNET: (process.env.USE_TESTNET || "false").toLowerCase() === "true",
  DELTA_TESTNET_URL: process.env.DELTA_TESTNET_URL || "https://cdn-ind.testnet.deltaex.org",
  DELTA_PROD_URL: process.env.DELTA_BASE_URL || "https://api.india.delta.exchange",

  getDeltaBaseUrl(): string {
    return this.USE_TESTNET ? this.DELTA_TESTNET_URL : this.DELTA_PROD_URL;
  },

  // CoinSwitch PRO (Exchange 2: 1:30 PM IST / 08:00 UTC Expiry)
  COINSWITCH_API_KEY: process.env.COINSWITCH_API_KEY || "",
  COINSWITCH_SECRET_KEY: process.env.COINSWITCH_SECRET_KEY || "",
  COINSWITCH_BASE_URL: process.env.COINSWITCH_BASE_URL || "https://coinswitch.co",
  COINSWITCH_PRO_URL: process.env.COINSWITCH_PRO_URL || "https://api-trading.coinswitch.co",

  // Global Lot Size (1 single variable for both exchanges)
  // 1 Delta BTC option contract = 0.001 BTC
  // CoinSwitch lot = BTC units
  TRADE_LOT_BTC: parseFloat(process.env.TRADE_LOT_BTC || "0.05"),

  // Underlying Asset & Pricing
  UNDERLYING_ASSET: (process.env.UNDERLYING_ASSET || "BTC").toUpperCase(),
  get UNDERLYING_SYMBOL(): string {
    return `${this.UNDERLYING_ASSET}USD`;
  },
  USD_INR_RATE: parseFloat(process.env.USD_INR_RATE || "86.50"),

  DELTA_EXPIRY_TIME_IST: "17:30",      // 5:30 PM IST (12:00 UTC)
  COINSWITCH_EXPIRY_TIME_IST: "13:30", // 1:30 PM IST (08:00 UTC)
  DAILY_ROLLOVER_TIME_IST: "13:30",

  NUM_OTM_STRIKES: parseInt(process.env.NUM_OTM_STRIKES || "5", 10),

  // Fund Balance & Rebalance Alert Settings
  FUND_IMBALANCE_THRESHOLD_PCT: parseFloat(process.env.FUND_IMBALANCE_THRESHOLD_PCT || "25.0"),
  BALANCE_CHECK_INTERVAL_MINUTES: parseInt(process.env.BALANCE_CHECK_INTERVAL_MINUTES || "30", 10),

  // Telegram Notifications
  TELEGRAM_ENABLED: (process.env.TELEGRAM_ENABLED || "true").toLowerCase() === "true",
  TELEGRAM_BOT_TOKEN: process.env.TELEGRAM_BOT_TOKEN || "",
  TELEGRAM_CHAT_ID: process.env.TELEGRAM_CHAT_ID || ""
};
