/**
 * Main entry point for the Options Arbitrage Bot (LIVE ONLY).
 * Delta Exchange (Long) ↔ CoinDCX (Short).
 */

import { Config } from "./config";
import { ArbTelegramBot } from "./telegramBot";

async function main() {
  console.log("============================================================");
  console.log("  OPTIONS ARBITRAGE BOT (LIVE DIRECT TRADING)");
  console.log("  Delta Exchange (Long) ↔ CoinDCX (Short)");
  console.log("============================================================");
  console.log(`  Mode: LIVE (Simulations Disabled)`);
  console.log(`  Telegram: ${Config.TELEGRAM_ENABLED ? "ENABLED" : "DISABLED"}`);
  console.log(`  Auto-Close: 1:30 PM IST daily`);
  console.log(`  Assets: BTC, ETH, XAUTUSDT`);
  console.log("============================================================");

  if (!Config.TELEGRAM_BOT_TOKEN) {
    console.error("❌ TELEGRAM_BOT_TOKEN not set in .env! Exiting.");
    process.exit(1);
  }

  const bot = new ArbTelegramBot();
  await bot.startPolling();

  // Graceful shutdown
  process.on("SIGINT", () => {
    console.log("\nShutting down bot gracefully...");
    bot.stop();
    process.exit(0);
  });

  process.on("SIGTERM", () => {
    console.log("\nShutting down bot gracefully...");
    bot.stop();
    process.exit(0);
  });
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
