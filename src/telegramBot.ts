/**
 * Interactive Telegram Bot for Live Crypto Options Arbitrage.
 *
 * Exchanges:
 *  - Delta Exchange (LONG / BUY): 10 lots = 0.1 unit
 *  - CoinSwitch PRO (SHORT / SELL): 1 lot = 0.1 unit
 *
 * Features:
 *  - Interactive inline buttons to scan and execute trades live
 *  - Pure price spreads and profit calculations (WITHOUT fee deductions)
 *  - Arbitrage Green Mark (🟢): Highlighted when Short Premium >= Long Premium
 *  - Live Net PnL tracking for positions on both exchanges
 *  - Live wallet balances (INR, USDT, Equity, Available Margin)
 *  - Daily automatic position close at 1:30 PM IST (08:00 UTC)
 */

import axios, { AxiosInstance } from "axios";
import cron from "node-cron";
import { Config, ASSETS, AssetConfig } from "./config";
import { ArbitrageEngine } from "./arbitrageEngine";
import { StrikeComparison, OptionChainResult, NetPnlReport, CombinedWalletReport } from "./types";

export interface InlineKeyboardButton {
  text: string;
  callback_data?: string;
  url?: string;
}

export class ArbTelegramBot {
  private token: string;
  private defaultChatId: string;
  private apiUrl: string;
  private http: AxiosInstance;
  private engine: ArbitrageEngine;
  private isRunning = false;
  private lastUpdateId = 0;

  constructor(engine?: ArbitrageEngine, token?: string, defaultChatId?: string) {
    this.token = token || Config.TELEGRAM_BOT_TOKEN;
    this.defaultChatId = defaultChatId || Config.TELEGRAM_CHAT_ID;
    this.apiUrl = this.token ? `https://api.telegram.org/bot${this.token}` : "";
    this.http = axios.create({ timeout: 30000 });
    this.engine = engine || new ArbitrageEngine();

    this.setupAutoClose();
  }

  /**
   * Schedule automatic position close at 1:30 PM IST daily (08:00 UTC).
   */
  private setupAutoClose(): void {
    cron.schedule(
      "0 8 * * *",
      async () => {
        console.log("⏰ 1:30 PM IST CUTOFF: Executing automated close of all open positions...");
        await this.autoCloseAllPositions();
      },
      { timezone: "UTC" }
    );

    console.log("✅ Auto-close scheduled: 1:30 PM IST (08:00 UTC) every day");
  }

  /**
   * Auto-close all positions and notify via Telegram.
   */
  public async autoCloseAllPositions(): Promise<void> {
    const chatId = this.defaultChatId;
    if (!chatId) return;

    try {
      await this.sendMessage(
        "🚨 *AUTO-CLOSE 1:30 PM IST* 🚨\n" +
          "━━━━━━━━━━━━━━━━━━━━━━\n" +
          "⏰ Daily 1:30 PM cutoff reached! Closing ALL open positions across Delta Exchange & CoinSwitch PRO...",
        chatId
      );

      const result = await this.engine.closeAllPositions();

      const lines = [
        "🛑 *DAILY 1:30 PM IST AUTO-CLOSE REPORT*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        `🏛 *Delta Exchange:* ${result.delta.closedCount} position(s) closed`,
      ];
      if (result.delta.details.length > 0) {
        lines.push(...result.delta.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No open positions on Delta.");
      }

      lines.push("");
      lines.push(`🏦 *CoinSwitch PRO:* ${result.coinswitch.closedCount} position(s) closed`);
      if (result.coinswitch.details.length > 0) {
        lines.push(...result.coinswitch.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No open positions on CoinSwitch.");
      }

      lines.push("\n✅ Both legs flat! Ready for next trade cycle.");
      lines.push("👉 Click /start to scan next-day arbitrage.");

      await this.sendMessage(lines.join("\n"), chatId);
    } catch (err: any) {
      await this.sendMessage(`❌ Auto-close error: ${err.message}`, chatId);
    }
  }

  /**
   * Send a formatted Telegram message with chunking and optional inline keyboard.
   */
  public async sendMessage(
    text: string,
    chatId?: string,
    inlineKeyboard?: InlineKeyboardButton[][]
  ): Promise<boolean> {
    const targetChat = chatId || this.defaultChatId;
    if (!this.apiUrl || !targetChat) {
      console.log(`[Telegram Log] ${text.substring(0, 150)}...`);
      return false;
    }

    const chunkSize = 3900;
    let success = true;

    for (let i = 0; i < text.length; i += chunkSize) {
      const chunk = text.substring(i, i + chunkSize);
      const isLastChunk = i + chunkSize >= text.length;

      const payload: any = {
        chat_id: targetChat,
        text: chunk,
        parse_mode: "Markdown",
      };

      if (isLastChunk && inlineKeyboard && inlineKeyboard.length > 0) {
        payload.reply_markup = { inline_keyboard: inlineKeyboard };
      }

      try {
        await this.http.post(`${this.apiUrl}/sendMessage`, payload);
      } catch (err: any) {
        try {
          delete payload.parse_mode;
          await this.http.post(`${this.apiUrl}/sendMessage`, payload);
        } catch (retryErr: any) {
          console.error(`Telegram send error: ${retryErr.message}`);
          success = false;
        }
      }
    }

    return success;
  }

  /**
   * Answer a callback query from an inline button press.
   */
  private async answerCallbackQuery(callbackQueryId: string, text?: string): Promise<void> {
    if (!this.apiUrl) return;
    try {
      await this.http.post(`${this.apiUrl}/answerCallbackQuery`, {
        callback_query_id: callbackQueryId,
        text: text || "",
        show_alert: false,
      });
    } catch {
      // Ignore ack errors
    }
  }

  // ─────────────────────────────────────────────────────────────
  // UI SCREENS & INTERACTIVE BUTTONS
  // ─────────────────────────────────────────────────────────────

  /**
   * Main Menu: Asset selection, Wallet, Net PnL, Close All.
   */
  public async sendMainMenu(chatId: string): Promise<void> {
    const keyboard: InlineKeyboardButton[][] = [
      [
        { text: "₿ BTC Options", callback_data: "asset_BTC" },
        { text: "⟠ ETH Options", callback_data: "asset_ETH" },
        { text: "🥇 XAUT Options", callback_data: "asset_XAUTUSDT" },
      ],
      [
        { text: "📈 Live Net PnL", callback_data: "view_pnl" },
        { text: "💼 Live Wallet", callback_data: "view_wallet" },
      ],
      [
        { text: "🛑 Close All Positions", callback_data: "close_all_confirm" },
      ],
    ];

    const text =
      "⚡ *OPTIONS ARBITRAGE BOT (LIVE)* ⚡\n" +
      "━━━━━━━━━━━━━━━━━━━━━━\n" +
      "🏛 *Delta Exchange:* LONG (BUY 10 lots = 0.1 unit)\n" +
      "🏦 *CoinSwitch PRO:* SHORT (SELL 1 lot = 0.1 unit)\n\n" +
      "🎯 *Strategy:* Short CoinSwitch (higher premium) & Long Delta (lower premium)\n" +
      "🟢 *Green Mark:* Short Premium ≥ Long Premium\n" +
      "⏰ *Auto-Close:* 1:30 PM IST daily (08:00 UTC)\n\n" +
      "Select an asset to scan or choose an action below:";

    await this.sendMessage(text, chatId, keyboard);
  }

  /**
   * Expiry Selection: Today's Expiry vs Tomorrow's Expiry.
   */
  public async showExpirySelection(chatId: string, assetKey: string): Promise<void> {
    const { today, tomorrow } = this.engine.getExpiryDates();
    const asset = ASSETS[assetKey];

    const keyboard: InlineKeyboardButton[][] = [
      [
        { text: `📅 Today (${today})`, callback_data: `expiry_${assetKey}_${today}` },
        { text: `📅 Tomorrow (${tomorrow})`, callback_data: `expiry_${assetKey}_${tomorrow}` },
      ],
      [
        { text: "🔙 Main Menu", callback_data: "main_menu" },
      ],
    ];

    const text =
      `🪙 *${asset.name} (${asset.symbol}) Options*\n` +
      `━━━━━━━━━━━━━━━━━━━━━━\n` +
      `📦 *Contract Sizing (0.1 units):*\n` +
      `   • CoinSwitch: *${asset.coinswitchLots} lot* = ${asset.lotSizeUnits} ${asset.symbol}\n` +
      `   • Delta: *${asset.deltaLots} lots* = ${asset.lotSizeUnits} ${asset.symbol}\n\n` +
      `Select expiry date to scan:`;

    await this.sendMessage(text, chatId, keyboard);
  }

  /**
   * Options Scanner Screen: Lists ATM & OTM Strikes with individual Execute buttons.
   */
  public async handleScanOptions(chatId: string, assetKey: string, expiryDate: string): Promise<void> {
    const asset = ASSETS[assetKey];

    await this.sendMessage(
      `🔍 *Scanning ${asset.symbol} live options for ${expiryDate}...*\n` +
        `_Fetching Delta Exchange & CoinSwitch PRO live books..._`,
      chatId
    );

    try {
      const results = await this.engine.scanOptions(assetKey, expiryDate);

      if (results.length === 0 || results[0].allStrikes.length === 0) {
        await this.sendMessage(
          `⚠️ No active options contracts found for *${asset.symbol}* expiring *${expiryDate}*.\n` +
            `Try selecting another expiry or checking market hours.`,
          chatId
        );
        return;
      }

      const chain = results[0];
      const spotStr = chain.spotPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const spotInr = Math.round(chain.spotPrice * Config.USD_INR_RATE).toLocaleString("en-IN");

      let text = `📊 *${asset.symbol} OPTIONS ARBITRAGE (LIVE)*\n`;
      text += `━━━━━━━━━━━━━━━━━━━━━━\n`;
      text += `🪙 *Spot:* *$${spotStr}* (₹${spotInr})\n`;
      text += `📅 *Expiry:* *${chain.expiryDate} (1:30 PM IST Cutoff)*\n`;
      text += `📦 *Trade Size:* *${asset.lotSizeUnits} ${asset.symbol}* (Delta: 10 lots | CoinSwitch: 1 lot)\n`;
      text += `━━━━━━━━━━━━━━━━━━━━━━\n\n`;

      // ── Suggested Trades (Short Premium >= Long Premium) ──
      if (chain.suggestedTrades.length > 0) {
        text += `🔥 *SUGGESTED TRADES (Short Premium ≥ Long Premium)* 🔥\n`;
        text += `_CoinSwitch short premium covers Delta long cost:_\n\n`;
        for (const t of chain.suggestedTrades.slice(0, 4)) {
          text += this.formatStrikeRow(t, true);
        }
        text += `\n`;
      } else {
        text += `ℹ️ _No strict CoinSwitch ≥ Delta spread right now. Market ATM/OTM rates below:_\n\n`;
      }

      // ── ATM Strikes ──
      text += `📍 *ATM STRIKES (Around ${chain.atmStrike.toLocaleString()})*\n`;
      text += `─────────────────────\n`;
      for (const c of chain.atmCalls) text += this.formatStrikeRow(c);
      for (const p of chain.atmPuts) text += this.formatStrikeRow(p);
      text += `\n`;

      // ── OTM Calls ──
      if (chain.otmCalls.length > 0) {
        text += `📈 *OTM CALLS (Above Spot)*\n`;
        text += `─────────────────────\n`;
        for (const c of chain.otmCalls) text += this.formatStrikeRow(c);
        text += `\n`;
      }

      // ── OTM Puts ──
      if (chain.otmPuts.length > 0) {
        text += `📉 *OTM PUTS (Below Spot)*\n`;
        text += `─────────────────────\n`;
        for (const p of chain.otmPuts) text += this.formatStrikeRow(p);
        text += `\n`;
      }

      text += `👉 *Tap any button below to execute the trade live:*`;

      // ── Clickable Execute Buttons ──
      const keyboard: InlineKeyboardButton[][] = [];

      for (const s of chain.allStrikes) {
        const tag = s.isArb ? "🟢 ARB" : "⚪";
        const sign = s.spreadUsd >= 0 ? "+" : "";
        const label = `${tag} ${s.strike.toLocaleString()} ${s.optionType} | Sprd: ${sign}$${s.spreadUsd.toFixed(2)}`;
        const cbData = `exec_${assetKey}_${s.strike}_${s.optionType}_${s.expiryDate}`;
        keyboard.push([{ text: label, callback_data: cbData }]);
      }

      // Action navigation buttons
      keyboard.push([
        { text: "📈 View Net PnL", callback_data: "view_pnl" },
        { text: "💼 View Wallet", callback_data: "view_wallet" },
      ]);
      keyboard.push([
        { text: "🔄 Refresh Scan", callback_data: `expiry_${assetKey}_${chain.expiryDate}` },
        { text: "🔙 Main Menu", callback_data: "main_menu" },
      ]);

      await this.sendMessage(text, chatId, keyboard);
    } catch (err: any) {
      await this.sendMessage(`❌ Scan error: ${err.message}`, chatId);
    }
  }

  /**
   * Strike row formatting without fees.
   */
  private formatStrikeRow(s: StrikeComparison, highlight = false): string {
    const icon = s.isArb ? "🟢" : "⚪";
    const spreadSign = s.spreadUsd >= 0 ? "+" : "";
    const spreadTag = s.spreadUsd >= 0 ? `+${s.spreadPct}%` : `${s.spreadPct}%`;

    let r = `${icon} *${s.strike.toLocaleString()} ${s.optionType}* (${s.category})\n`;
    r += `   • Delta (Long BUY): *$${s.deltaPrice.toFixed(2)}* (IV: ${s.deltaIv.toFixed(1)}%)\n`;
    r += `   • CoinSwitch (Short SELL): *$${s.coinswitchPrice.toFixed(2)}* (IV: ${s.coinswitchIv.toFixed(1)}%)\n`;
    r += `   • Spread: *${spreadSign}$${s.spreadUsd.toFixed(2)}* (${spreadTag})\n`;
    r += `   • Profit (${s.underlyingUnits} units): *${spreadSign}$${s.potentialProfitUsd.toFixed(2)}* (₹${s.potentialProfitInr.toLocaleString("en-IN")})${s.isArb ? " 🟢 [ARB]" : ""}\n`;
    return r;
  }

  /**
   * Execute Trade Handler: Fired when user clicks any strike button!
   */
  public async handleExecuteTrade(chatId: string, callbackData: string): Promise<void> {
    const parts = callbackData.replace("exec_", "").split("_");
    if (parts.length < 4) {
      await this.sendMessage("❌ Invalid execution parameters", chatId);
      return;
    }

    const assetKey = parts[0];
    const strikeNum = parseFloat(parts[1]);
    const optionType = parts[2] as "CALL" | "PUT";
    const expiryDate = parts.slice(3).join("-");

    const asset = ASSETS[assetKey];
    if (!asset) {
      await this.sendMessage(`❌ Unknown asset: ${assetKey}`, chatId);
      return;
    }

    await this.sendMessage(
      `⚡ *EXECUTING LIVE ARBITRAGE TRADE...*\n` +
        `━━━━━━━━━━━━━━━━━━━━━━\n` +
        `🪙 *${asset.symbol}* | Strike: *${strikeNum.toLocaleString()} ${optionType}*\n` +
        `📅 Expiry: *${expiryDate}*\n\n` +
        `🏛 *Delta Exchange:* BUY ${asset.deltaLots} lots (LONG)\n` +
        `🏦 *CoinSwitch PRO:* SELL ${asset.coinswitchLots} lot(s) (SHORT)\n` +
        `📦 Total Size: *${asset.lotSizeUnits} ${asset.symbol}*\n\n` +
        `_Dispatching live signed API orders to exchanges..._`,
      chatId
    );

    try {
      const results = await this.engine.scanOptions(assetKey, expiryDate);
      const chain = results[0];
      if (!chain) {
        await this.sendMessage(`❌ Could not refresh options pricing for ${expiryDate}`, chatId);
        return;
      }

      const strikeData = chain.allStrikes.find(
        (s) => s.strike === strikeNum && s.optionType === optionType
      );

      if (!strikeData) {
        await this.sendMessage(`❌ Strike ${strikeNum} ${optionType} not found in live books`, chatId);
        return;
      }

      // Execute live trade
      const trade = await this.engine.executeTrade(assetKey, strikeData);

      const statusEmoji = trade.status === "filled" ? "✅" : trade.status === "partial" ? "⚠️" : "❌";
      const lines = [
        `${statusEmoji} *LIVE TRADE EXECUTION RESULT*`,
        `━━━━━━━━━━━━━━━━━━━━━━`,
        `🆔 Trade Ref: \`${trade.id}\``,
        `🪙 Asset: *${asset.symbol}* | *${strikeNum.toLocaleString()} ${optionType}*`,
        `📅 Expiry: *${expiryDate}*`,
        `📦 Sizing: *${asset.lotSizeUnits} ${asset.symbol}*`,
        ``,
        `🏛 *Delta Exchange (LONG):* ${trade.deltaOrderResult?.success ? "✅ FILLED" : "❌ FAILED"}`,
        `   • Details: ${trade.deltaOrderResult?.message || "No response"}`,
        ``,
        `🏦 *CoinSwitch PRO (SHORT):* ${trade.coinswitchOrderResult?.success ? "✅ FILLED" : "❌ FAILED"}`,
        `   • Details: ${trade.coinswitchOrderResult?.message || "No response"}`,
        ``,
        `🎯 *Target Spread Profit:* 🟢 *+$${trade.potentialProfitUsd.toFixed(2)}* (₹${trade.potentialProfitInr.toLocaleString("en-IN")})`,
      ];

      if (trade.error) {
        lines.push(`\n⚠️ *Warning:* ${trade.error}`);
      }

      lines.push(`\n⏰ *Auto-Close:* Scheduled for 1:30 PM IST daily.`);

      const keyboard: InlineKeyboardButton[][] = [
        [
          { text: "📈 View Live Net PnL", callback_data: "view_pnl" },
          { text: "💼 View Wallet", callback_data: "view_wallet" },
        ],
        [
          { text: "🛑 Close All Positions", callback_data: "close_all_confirm" },
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMessage(lines.join("\n"), chatId, keyboard);
    } catch (err: any) {
      await this.sendMessage(`❌ Execution failed: ${err.message}`, chatId);
    }
  }

  /**
   * Live Net PnL & Open Positions Screen.
   */
  public async handleShowNetPnl(chatId: string): Promise<void> {
    await this.sendMessage("📊 *Fetching Live Net PnL & Positions from Delta & CoinSwitch...*", chatId);

    try {
      const pnlReport: NetPnlReport = await this.engine.getLiveNetPnl();

      const pnlSign = pnlReport.totalUnrealizedUsd >= 0 ? "+" : "";
      const pnlEmoji = pnlReport.totalUnrealizedUsd > 0 ? "🟢" : pnlReport.totalUnrealizedUsd < 0 ? "🔴" : "⚪";

      const lines = [
        `📈 *LIVE NET PnL & POSITIONS REPORT*`,
        `━━━━━━━━━━━━━━━━━━━━━━`,
        `🕒 Time: ${new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })} IST`,
        `💰 *COMBINED NET PnL:* ${pnlEmoji} *${pnlSign}$${pnlReport.totalUnrealizedUsd.toFixed(2)}* (₹${pnlReport.totalUnrealizedInr.toLocaleString("en-IN")})`,
        `━━━━━━━━━━━━━━━━━━━━━━\n`,
        `🏛 *Delta Exchange Positions:*`,
      ];

      if (pnlReport.deltaPositions.length === 0) {
        lines.push("   • _No open positions (Flat)_");
      } else {
        for (const p of pnlReport.deltaPositions) {
          const sign = p.unrealizedPnlUsd >= 0 ? "+" : "";
          lines.push(
            `   • *${p.symbol}*: ${p.side} ${p.size} lots\n` +
              `     Entry: $${p.entryPrice.toFixed(2)} | Mark: $${p.markPrice.toFixed(2)}\n` +
              `     PnL: *${sign}$${p.unrealizedPnlUsd.toFixed(2)}* (₹${p.unrealizedPnlInr.toLocaleString("en-IN")})`
          );
        }
        lines.push(`   👉 *Delta Subtotal:* ${pnlReport.deltaUnrealizedUsd >= 0 ? "+" : ""}$${pnlReport.deltaUnrealizedUsd.toFixed(2)}`);
      }

      lines.push(`\n🏦 *CoinSwitch PRO Positions:*`);
      if (pnlReport.coinswitchPositions.length === 0) {
        lines.push("   • _No open positions (Flat)_");
      } else {
        for (const p of pnlReport.coinswitchPositions) {
          const sign = p.unrealizedPnlUsd >= 0 ? "+" : "";
          lines.push(
            `   • *${p.symbol}*: ${p.side} ${p.size} lots\n` +
              `     Entry: $${p.entryPrice.toFixed(2)} | Mark: $${p.markPrice.toFixed(2)}\n` +
              `     PnL: *${sign}$${p.unrealizedPnlUsd.toFixed(2)}* (₹${p.unrealizedPnlInr.toLocaleString("en-IN")})`
          );
        }
        lines.push(`   👉 *CoinSwitch Subtotal:* ${pnlReport.coinswitchUnrealizedUsd >= 0 ? "+" : ""}$${pnlReport.coinswitchUnrealizedUsd.toFixed(2)}`);
      }

      lines.push(
        `\n📊 *Status:* ${pnlReport.isFlat ? "🟢 FLAT (Ready for entry)" : "🟡 ACTIVE ARBITRAGE IN PROGRESS"}`
      );
      lines.push(`⏰ *Auto-Close:* All positions automatically close at 1:30 PM IST.`);

      const keyboard: InlineKeyboardButton[][] = [
        [
          { text: "🔄 Refresh Net PnL", callback_data: "view_pnl" },
          { text: "🛑 Close All Positions", callback_data: "close_all_confirm" },
        ],
        [
          { text: "💼 View Wallet", callback_data: "view_wallet" },
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMessage(lines.join("\n"), chatId, keyboard);
    } catch (err: any) {
      await this.sendMessage(`❌ Error fetching Net PnL: ${err.message}`, chatId);
    }
  }

  /**
   * Live Wallet Balances Screen.
   */
  public async handleShowWallet(chatId: string): Promise<void> {
    await this.sendMessage("💼 *Fetching Live Wallet Balances from Delta & CoinSwitch...*", chatId);

    try {
      const report: CombinedWalletReport = await this.engine.getLiveWalletBalances();

      const lines = [
        `💼 *LIVE WALLET BALANCES & MARGIN*`,
        `━━━━━━━━━━━━━━━━━━━━━━`,
        `💰 *Total Combined Equity:* *$${report.totalBalanceUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${report.totalBalanceInr.toLocaleString("en-IN")})`,
        `🛡 *Available Margin:* *$${report.totalAvailableUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${report.totalAvailableInr.toLocaleString("en-IN")})`,
        `━━━━━━━━━━━━━━━━━━━━━━\n`,
        `🏛 *Delta Exchange (Live):*`,
        `   • Total Balance: *$${report.delta.balanceUsd.toFixed(2)}* (₹${report.delta.balanceInr.toLocaleString("en-IN")})`,
        `   • Available Margin: *$${report.delta.availableUsd.toFixed(2)}*`,
      ];

      if (report.delta.currencies.length > 0) {
        for (const c of report.delta.currencies) {
          lines.push(`   • ${c.symbol}: \`${c.total.toFixed(4)}\` (Avail: \`${c.available.toFixed(4)}\`)`);
        }
      }
      if (report.delta.error) {
        lines.push(`   • ⚠️ Status: ${report.delta.error}`);
      }

      lines.push(`\n🏦 *CoinSwitch PRO (Live):*`);
      lines.push(
        `   • Total Balance: *$${report.coinswitch.balanceUsd.toFixed(2)}* (₹${report.coinswitch.balanceInr.toLocaleString("en-IN")})`
      );
      lines.push(
        `   • Available Margin: *$${report.coinswitch.availableUsd.toFixed(2)}* (₹${report.coinswitch.availableInr.toLocaleString("en-IN")})`
      );

      if (report.coinswitch.currencies && report.coinswitch.currencies.length > 0) {
        for (const c of report.coinswitch.currencies) {
          lines.push(`   • ${c.symbol}: \`${c.total.toFixed(4)}\` (Avail: \`${c.available.toFixed(4)}\`)`);
        }
      }
      if (report.coinswitch.error) {
        lines.push(`   • ⚠️ Status: ${report.coinswitch.error}`);
      }

      const keyboard: InlineKeyboardButton[][] = [
        [
          { text: "🔄 Refresh Wallet", callback_data: "view_wallet" },
          { text: "📈 View Net PnL", callback_data: "view_pnl" },
        ],
        [
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMessage(lines.join("\n"), chatId, keyboard);
    } catch (err: any) {
      await this.sendMessage(`❌ Error fetching wallet: ${err.message}`, chatId);
    }
  }

  /**
   * Confirmation prompt before closing all positions.
   */
  public async handleCloseAllConfirm(chatId: string): Promise<void> {
    const keyboard: InlineKeyboardButton[][] = [
      [
        { text: "🚨 YES, CLOSE EVERYTHING NOW", callback_data: "close_all_execute" },
      ],
      [
        { text: "❌ Cancel", callback_data: "main_menu" },
      ],
    ];

    const text =
      "⚠️ *CONFIRM IMMEDIATE CLOSE ALL POSITIONS* ⚠️\n" +
      "━━━━━━━━━━━━━━━━━━━━━━\n" +
      "This will send market close orders for ALL active options positions on:\n" +
      " • Delta Exchange (India)\n" +
      " • CoinSwitch PRO\n\n" +
      "Are you sure you want to flatten all positions immediately?";

    await this.sendMessage(text, chatId, keyboard);
  }

  /**
   * Execute immediate close of all positions.
   */
  public async handleCloseAllExecute(chatId: string): Promise<void> {
    await this.sendMessage("🛑 *Closing all positions across Delta & CoinSwitch now...*", chatId);

    try {
      const result = await this.engine.closeAllPositions();

      const lines = [
        "✅ *POSITIONS CLOSED REPORT*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        `🏛 Delta Exchange: ${result.delta.closedCount} closed`,
        ...result.delta.details.map((d) => `  • ${d}`),
        `🏦 CoinSwitch PRO: ${result.coinswitch.closedCount} closed`,
        ...result.coinswitch.details.map((d) => `  • ${d}`),
        "\nAll active legs are now FLAT.",
      ];

      const keyboard: InlineKeyboardButton[][] = [
        [
          { text: "📈 Verify Net PnL", callback_data: "view_pnl" },
          { text: "💼 View Wallet", callback_data: "view_wallet" },
        ],
        [
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMessage(lines.join("\n"), chatId, keyboard);
    } catch (err: any) {
      await this.sendMessage(`❌ Close all error: ${err.message}`, chatId);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // POLLING & EVENT LOOP
  // ─────────────────────────────────────────────────────────────

  /**
   * Start long-polling Telegram for incoming messages and button clicks.
   */
  public async startPolling(): Promise<void> {
    if (!this.apiUrl) {
      console.warn("Telegram bot token not provided. Bot polling skipped.");
      return;
    }

    this.isRunning = true;
    console.log("🤖 Telegram Bot polling started. Listening for commands and button clicks...");

    if (this.defaultChatId) {
      await this.sendMainMenu(this.defaultChatId);
    }

    while (this.isRunning) {
      try {
        const resp = await this.http.get(`${this.apiUrl}/getUpdates`, {
          params: {
            offset: this.lastUpdateId + 1,
            timeout: 20,
          },
        });

        const updates: any[] = resp.data?.result || [];

        for (const u of updates) {
          this.lastUpdateId = Math.max(this.lastUpdateId, u.update_id);

          if (u.callback_query) {
            const cb = u.callback_query;
            const cbId = cb.id;
            const data: string = cb.data || "";
            const chatId = cb.message?.chat?.id?.toString() || this.defaultChatId;

            await this.answerCallbackQuery(cbId, "Processing...");

            if (data === "main_menu") {
              await this.sendMainMenu(chatId);
            } else if (data.startsWith("asset_")) {
              const assetKey = data.replace("asset_", "");
              await this.showExpirySelection(chatId, assetKey);
            } else if (data.startsWith("expiry_")) {
              const parts = data.replace("expiry_", "").split("_");
              const assetKey = parts[0];
              const expDate = parts.slice(1).join("-");
              await this.handleScanOptions(chatId, assetKey, expDate);
            } else if (data.startsWith("exec_")) {
              await this.handleExecuteTrade(chatId, data);
            } else if (data === "view_pnl") {
              await this.handleShowNetPnl(chatId);
            } else if (data === "view_wallet") {
              await this.handleShowWallet(chatId);
            } else if (data === "close_all_confirm") {
              await this.handleCloseAllConfirm(chatId);
            } else if (data === "close_all_execute") {
              await this.handleCloseAllExecute(chatId);
            }
          }

          if (u.message && u.message.text) {
            const text: string = u.message.text.trim().toLowerCase();
            const chatId = u.message.chat.id.toString();

            if (text === "/start" || text === "start" || text === "menu") {
              await this.sendMainMenu(chatId);
            } else if (text === "/pnl" || text === "pnl") {
              await this.handleShowNetPnl(chatId);
            } else if (text === "/wallet" || text === "wallet" || text === "balance") {
              await this.handleShowWallet(chatId);
            } else if (text === "/close" || text === "close") {
              await this.handleCloseAllConfirm(chatId);
            } else if (text.includes("btc")) {
              await this.showExpirySelection(chatId, "BTC");
            } else if (text.includes("eth")) {
              await this.showExpirySelection(chatId, "ETH");
            } else if (text.includes("xaut") || text.includes("gold")) {
              await this.showExpirySelection(chatId, "XAUTUSDT");
            } else {
              await this.sendMainMenu(chatId);
            }
          }
        }
      } catch (err: any) {
        if (!err.message?.includes("timeout")) {
          console.warn(`Telegram polling warning: ${err.message}`);
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
  }

  public stop(): void {
    this.isRunning = false;
  }
}
