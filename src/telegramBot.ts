/**
 * Telegram Bot Service (TypeScript / Node.js).
 * Listens for incoming chat commands ("options", "balance", "trade", "arb")
 * and pushes daily 1:30 PM alerts & fund imbalance notifications.
 */

import axios from "axios";
import { Config } from "./config";
import { ArbitrageEngine } from "./arbitrageEngine";
import { BalanceMonitor } from "./balanceMonitor";

export class TelegramBot {
  private token: string;
  private defaultChatId: string;
  private apiUrl: string;
  private arbitrageEngine: ArbitrageEngine;
  private balanceMonitor: BalanceMonitor;
  private isRunning = false;
  private lastUpdateId = 0;

  constructor(
    arbitrageEngine?: ArbitrageEngine,
    balanceMonitor?: BalanceMonitor,
    token?: string,
    defaultChatId?: string
  ) {
    this.token = token || Config.TELEGRAM_BOT_TOKEN;
    this.defaultChatId = defaultChatId || Config.TELEGRAM_CHAT_ID;
    this.apiUrl = this.token ? `https://api.telegram.org/bot${this.token}` : "";
    this.arbitrageEngine = arbitrageEngine || new ArbitrageEngine();
    this.balanceMonitor = balanceMonitor || new BalanceMonitor();
  }

  /**
   * Send a formatted Telegram message (with automatic message length chunking).
   */
  public async sendMessage(text: string, chatId?: string): Promise<boolean> {
    const targetChat = chatId || this.defaultChatId;
    if (!this.apiUrl || !targetChat) {
      console.log(`[Telegram Mock] ${text.substring(0, 100)}...`);
      return false;
    }

    const chunkSize = 3900;
    let success = true;

    for (let i = 0; i < text.length; i += chunkSize) {
      const chunk = text.substring(i, i + chunkSize);
      try {
        await axios.post(`${this.apiUrl}/sendMessage`, {
          chat_id: targetChat,
          text: chunk,
          parse_mode: "Markdown"
        }, { timeout: 12000 });
      } catch (err: any) {
        // Retry without Markdown if syntax parsing failed
        try {
          await axios.post(`${this.apiUrl}/sendMessage`, {
            chat_id: targetChat,
            text: chunk
          }, { timeout: 12000 });
        } catch (retryErr: any) {
          console.error(`Error sending Telegram message: ${retryErr.message}`);
          success = false;
        }
      }
    }

    return success;
  }

  /**
   * Process incoming user commands.
   */
  public async processCommand(text: string, userChatId: string): Promise<void> {
    const raw = text.trim().toLowerCase();

    // 1. 'options' command
    if (raw.includes("option")) {
      await this.sendMessage("🔍 *Scanning Delta & CoinSwitch options market...*", userChatId);
      try {
        const scanRes = await this.arbitrageEngine.scanOptions();
        const report = this.arbitrageEngine.formatTelegramReport(scanRes);
        await this.sendMessage(report, userChatId);
      } catch (err: any) {
        await this.sendMessage(`❌ Error scanning options: ${err.message}`, userChatId);
      }
      return;
    }

    // 2. 'balance' command
    if (raw.includes("balance")) {
      await this.sendMessage("💼 *Fetching wallet balances from Delta and CoinSwitch...*", userChatId);
      try {
        const balRes = await this.balanceMonitor.checkBalances();
        const report = this.balanceMonitor.formatBalanceReport(balRes);
        await this.sendMessage(report, userChatId);
      } catch (err: any) {
        await this.sendMessage(`❌ Error checking balances: ${err.message}`, userChatId);
      }
      return;
    }

    // 3. 'arb' command (Arbitrage signals only)
    if (raw.includes("arb")) {
      try {
        const scanRes = await this.arbitrageEngine.scanOptions();
        const arbs = scanRes.arbitrageOpportunities;
        if (arbs.length === 0) {
          await this.sendMessage(
            "ℹ️ No strict *CoinSwitch ≥ Delta* arbitrage found right now.\n" +
            "Send *options* to view full ATM & OTM comparisons.",
            userChatId
          );
        } else {
          const lines = [`🔥 *FOUND ${arbs.length} ARBITRAGE SIGNALS (CoinSwitch ≥ Delta)* 🔥\n`];
          for (const a of arbs) {
            lines.push(
              `🟢 *${a.strike.toLocaleString()} ${a.optionType}* (${a.category})\n` +
              `   • CoinSwitch (1:30 PM): *$${a.coinswitchPrice.toFixed(2)}* | IV: ${a.coinswitchIv}%\n` +
              `   • Delta (5:30 PM): *$${a.deltaPrice.toFixed(2)}* | IV: ${a.deltaIv}%\n` +
              `   • Spread: *+$${a.diff.toFixed(2)}* (+${a.diffPct}%) | IV Edge: +${a.ivDiff}%\n` +
              `   👉 *Action:* SELL CoinSwitch / BUY Delta\n`
            );
          }
          await this.sendMessage(lines.join("\n"), userChatId);
        }
      } catch (err: any) {
        await this.sendMessage(`❌ Error: ${err.message}`, userChatId);
      }
      return;
    }

    // 4. 'trade' or 'rollover' command
    if (raw.includes("trade") || raw.includes("rollover")) {
      const nowUtc = new Date();
      const istNow = new Date(nowUtc.getTime() + 5.5 * 3600 * 1000);

      const cutoff = new Date(istNow);
      cutoff.setUTCHours(13, 30, 0, 0);
      if (istNow > cutoff) {
        cutoff.setUTCDate(cutoff.getUTCDate() + 1);
      }

      const diffMs = cutoff.getTime() - istNow.getTime();
      const hours = Math.floor(diffMs / (3600 * 1000));
      const mins = Math.floor((diffMs % (3600 * 1000)) / (60 * 1000));

      const lines = [
        "⏰ *1:30 PM IST DAILY TRADE EXECUTION* ⏰",
        "━━━━━━━━━━━━━━━━━━━━━━",
        `🕒 Current IST: *${istNow.toISOString().substring(11, 16)} IST*`,
        `⏳ Time to next 1:30 PM cutoff: *${hours}h ${mins}m*\n`,
        "📋 *DAILY EXECUTION PROCEDURE:*",
        "1. *1:30 PM IST Close:*",
        "   • CoinSwitch options settle automatically at 1:30 PM IST (08:00 UTC).",
        "   • Verify settlement / close yesterday's trade.",
        "2. *1:30 PM IST Next-Day Entry:*",
        "   • Send *options* to scan active next-day mispriced strikes.",
        "   • Select Strike where CoinSwitch Price ≥ Delta Price.",
        "   • Sell the overpriced CoinSwitch contract (or hedge on Delta).",
        "3. *Capital Check:*",
        "   • Type `/balance` to confirm both exchanges have balanced margin."
      ];
      await this.sendMessage(lines.join("\n"), userChatId);
      return;
    }

    // 5. 'status' command
    if (raw.includes("status")) {
      try {
        const spot = await this.arbitrageEngine.deltaClient.getSpotPrice();
        const nowUtc = new Date();
        const istNow = new Date(nowUtc.getTime() + 5.5 * 3600 * 1000);
        const lines = [
          "🟢 *OPTIONS ARBITRAGE BOT: ONLINE (Node.js)*",
          "━━━━━━━━━━━━━━━━━━━━━━",
          `🪙 ${Config.UNDERLYING_ASSET} Spot: *$${spot.toLocaleString("en-US", { minimumFractionDigits: 2 })}*`,
          `⏰ Time: *${istNow.toISOString().replace("T", " ").substring(0, 19)} IST*`,
          "🏛 Delta Expiry Rule: 5:30 PM IST (12:00 UTC)",
          "🏦 CoinSwitch Expiry Rule: 1:30 PM IST (08:00 UTC)",
          `⚖️ Fund Imbalance Limit: ${Config.FUND_IMBALANCE_THRESHOLD_PCT}%\n`,
          "👉 Send *options* to run an instant scan!"
        ];
        await this.sendMessage(lines.join("\n"), userChatId);
      } catch (err: any) {
        await this.sendMessage(`⚠️ Status check error: ${err.message}`, userChatId);
      }
      return;
    }

    // 6. 'help' / default
    const helpLines = [
      "🤖 *OPTIONS IV ARBITRAGE BOT COMMANDS (Node.js)*",
      "━━━━━━━━━━━━━━━━━━━━━━",
      "• *options* — Scan Delta & CoinSwitch ATM & OTM options and show arbitrage opportunities where CoinSwitch ≥ Delta.",
      "• *arb* — Show ONLY active arbitrage signals.",
      "• *balance* — Check wallet balances on Delta & CoinSwitch, with transfer alerts if imbalanced.",
      "• *trade* — 1:30 PM IST rollover execution instructions.",
      "• *status* — Check live spot price and bot status.",
      "• *help* — Show this instructions menu.\n",
      "💡 *STRATEGY SUMMARY:*",
      "_Delta options expire at 5:30 PM IST (more time to expiry). CoinSwitch options expire at 1:30 PM IST (less time to expiry). When CoinSwitch ≥ Delta, CoinSwitch IV is inflated—creating an arbitrage harvest!_"
    ];
    await this.sendMessage(helpLines.join("\n"), userChatId);
  }

  /**
   * Start long-polling Telegram getUpdates loop.
   */
  public async startPolling(): Promise<void> {
    if (!this.apiUrl) {
      console.log("Telegram Bot Token not configured. Polling disabled.");
      return;
    }

    this.isRunning = true;
    console.log("Telegram bot polling started...");

    while (this.isRunning) {
      try {
        const resp = await axios.get(`${this.apiUrl}/getUpdates`, {
          params: {
            offset: this.lastUpdateId + 1,
            timeout: 20
          },
          timeout: 25000
        });

        const updates: any[] = resp.data?.result || [];
        for (const update of updates) {
          this.lastUpdateId = Math.max(this.lastUpdateId, update.update_id || 0);

          const msg = update.message || update.channel_post;
          if (!msg || !msg.text) continue;

          const text = msg.text;
          const fromChat = msg.chat?.id ? msg.chat.id.toString() : "";

          console.log(`Received Telegram command: '${text}' from chat ${fromChat}`);
          await this.processCommand(text, fromChat);
        }
      } catch (err: any) {
        if (err.response?.status === 409) {
          console.warn("Telegram conflict (another instance polling). Waiting 5s...");
          await new Promise((r) => setTimeout(r, 5000));
        } else if (err.code !== "ECONNABORTED") {
          await new Promise((r) => setTimeout(r, 3000));
        }
      }
    }
  }

  public stop(): void {
    this.isRunning = false;
  }
}

