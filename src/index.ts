/**
 * Main application entrypoint (TypeScript / Node.js).
 * Coordinates Telegram Bot, Options Arbitrage Scanner, and Balance Monitor.
 */

import { Config } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinSwitchClient } from "./coinSwitchClient";
import { ArbitrageEngine } from "./arbitrageEngine";
import { BalanceMonitor } from "./balanceMonitor";
import { TelegramBot } from "./telegramBot";

class OptionsAlgoApp {
  public deltaClient: DeltaClient;
  public coinSwitchClient: CoinSwitchClient;
  public arbitrageEngine: ArbitrageEngine;
  public balanceMonitor: BalanceMonitor;
  public telegramBot: TelegramBot;

  private lastImbalanceAlertTime = 0;
  private lastRolloverAlertDate = "";

  constructor() {
    this.deltaClient = new DeltaClient();
    this.coinSwitchClient = new CoinSwitchClient();
    this.arbitrageEngine = new ArbitrageEngine(this.deltaClient, this.coinSwitchClient);
    this.balanceMonitor = new BalanceMonitor(this.deltaClient, this.coinSwitchClient);
    this.telegramBot = new TelegramBot(this.arbitrageEngine, this.balanceMonitor);
  }

  public async runScan(): Promise<string> {
    console.log("Executing options scan across Delta and CoinSwitch...");
    const res = await this.arbitrageEngine.scanOptions();
    return this.arbitrageEngine.formatTelegramReport(res);
  }

  public async runBalance(): Promise<string> {
    console.log("Checking wallet balances...");
    const res = await this.balanceMonitor.checkBalances();
    return this.balanceMonitor.formatBalanceReport(res);
  }

  public async checkFundImbalance(): Promise<void> {
    try {
      const bal = await this.balanceMonitor.checkBalances();
      if (bal.isImbalanced) {
        const now = Date.now();
        // Send alert once per hour if condition persists
        if (now - this.lastImbalanceAlertTime > 3600 * 1000) {
          console.warn("Fund imbalance detected! Sending Telegram alert...");
          const msg = this.balanceMonitor.formatBalanceReport(bal);
          await this.telegramBot.sendMessage(msg);
          this.lastImbalanceAlertTime = now;
        }
      }
    } catch (err: any) {
      console.error(`Error checking balance: ${err.message}`);
    }
  }

  public async checkDailyRollover(): Promise<void> {
    const nowUtc = new Date();
    const istNow = new Date(nowUtc.getTime() + 5.5 * 3600 * 1000);
    const currentDateStr = istNow.toISOString().split("T")[0];

    const hour = istNow.getUTCHours();
    const min = istNow.getUTCMinutes();

    // Trigger window: 13:29 to 13:35 IST
    if (hour === 13 && min >= 29 && min <= 35) {
      if (this.lastRolloverAlertDate !== currentDateStr) {
        console.log("1:30 PM IST reached! Sending daily rollover and trade scan alert...");
        const header =
          "🚨 *1:30 PM IST DAILY ROLLOVER ALERT* 🚨\n" +
          "━━━━━━━━━━━━━━━━━━━━━━\n" +
          "1. *CoinSwitch trade has expired / settled.* Review closed position.\n" +
          "2. *Scanning active Next-Day Expiry options below:* 👇\n\n";

        const scanRes = await this.arbitrageEngine.scanOptions();
        const report = this.arbitrageEngine.formatTelegramReport(scanRes);
        await this.telegramBot.sendMessage(header + report);
        this.lastRolloverAlertDate = currentDateStr;
      }
    }
  }

  public async startService(): Promise<void> {
    console.log("============================================================");
    console.log("OPTIONS IV ARBITRAGE BOT (TypeScript / Node.js) STARTED");
    console.log(`Underlying Asset: ${Config.UNDERLYING_ASSET}`);
    console.log(`Delta Expiry: ${Config.DELTA_EXPIRY_TIME_IST} | CoinSwitch Expiry: ${Config.COINSWITCH_EXPIRY_TIME_IST}`);
    console.log(`Daily Rollover Time: ${Config.DAILY_ROLLOVER_TIME_IST} IST`);
    console.log(`Imbalance Threshold: ${Config.FUND_IMBALANCE_THRESHOLD_PCT}%`);
    console.log("============================================================");

    // 1. Send Startup message & Start Telegram Polling
    if (Config.TELEGRAM_BOT_TOKEN) {
      this.telegramBot.sendMessage(
        "🚀 *Options Arbitrage Bot Online (Node.js / TS)!*\n" +
        "• Send *options* to get Delta vs CoinSwitch ATM/OTM comparison\n" +
        "• Send *balance* to check account margin & fund imbalance alerts\n" +
        "• Strategy: IV Arbitrage (CoinSwitch ≥ Delta)"
      );
      this.telegramBot.startPolling();
    } else {
      console.warn("TELEGRAM_BOT_TOKEN not provided in .env. Bot polling disabled.");
    }

    // 2. Background periodic checks
    const balIntervalMs = Config.BALANCE_CHECK_INTERVAL_MINUTES * 60 * 1000;
    setInterval(() => this.checkFundImbalance(), balIntervalMs);
    setInterval(() => this.checkDailyRollover(), 15 * 1000);

    // Immediate initial balance check
    await this.checkFundImbalance();
  }
}

async function main() {
  const args = process.argv.slice(2);
  const app = new OptionsAlgoApp();

  if (args.includes("--options") || args.includes("--scan")) {
    const report = await app.runScan();
    console.log(report);
    process.exit(0);
  }

  if (args.includes("--balance")) {
    const report = await app.runBalance();
    console.log(report);
    process.exit(0);
  }

  if (args.includes("--test-telegram")) {
    console.log("Sending test Telegram message...");
    const success = await app.telegramBot.sendMessage(
      "🔔 *Test notification from TypeScript / Node.js Options Arbitrage Bot!* Integration is working."
    );
    if (success) {
      console.log("Telegram message sent successfully!");
    } else {
      console.error("Failed to send message. Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.");
    }
    process.exit(0);
  }

  // Default: start full service
  await app.startService();
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});

