"use strict";
/**
 * Main entry point for the Options Arbitrage Bot (LIVE ONLY).
 * Delta Exchange (Long) ↔ CoinSwitch PRO (Short).
 */
Object.defineProperty(exports, "__esModule", { value: true });
const config_1 = require("./config");
const telegramBot_1 = require("./telegramBot");
async function main() {
    console.log("============================================================");
    console.log("  OPTIONS ARBITRAGE BOT (LIVE DIRECT TRADING)");
    console.log("  Delta Exchange (Long) ↔ CoinSwitch PRO (Short)");
    console.log("============================================================");
    console.log(`  Mode: LIVE (Simulations Disabled)`);
    console.log(`  Telegram: ${config_1.Config.TELEGRAM_ENABLED ? "ENABLED" : "DISABLED"}`);
    console.log(`  Fees Deducted: Delta (${config_1.Config.DELTA_FEE_PCT}%) + CoinSwitch (${config_1.Config.COINSWITCH_FEE_PCT}%)`);
    console.log(`  Arb Signal: Green Mark (🟢) ONLY when Net Profit > 0 after all fees`);
    console.log(`  Auto-Close: 1:30 PM IST daily (08:00 UTC)`);
    console.log(`  Assets: BTC, ETH, XAUTUSDT`);
    console.log("============================================================");
    if (!config_1.Config.TELEGRAM_BOT_TOKEN) {
        console.error("❌ TELEGRAM_BOT_TOKEN not set in .env! Exiting.");
        process.exit(1);
    }
    const bot = new telegramBot_1.ArbTelegramBot();
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
