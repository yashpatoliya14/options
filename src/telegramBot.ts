/**
 * Interactive Telegram Bot for Live Crypto Options Arbitrage.
 *
 * Exchanges:
 *  - Delta Exchange (LONG / BUY): 10 lots = 0.1 unit
 *  - CoinDCX (SHORT / SELL): 1 lot = 0.1 unit
 *
 * Features:
 *  - Interactive inline buttons to scan and execute trades
 *  - Net PnL tracking for active positions on both exchanges
 *  - Live wallet balance display
 *  - Suggestions where Short premium (CoinDCX) >= Long premium (Delta)
 *  - Daily automatic position close at 1:30 PM IST
 */

import TelegramBot = require("node-telegram-bot-api");
import cron from "node-cron";
import { Config, ASSETS, AssetConfig } from "./config";
import { ArbitrageEngine } from "./arbitrageEngine";
import { StrikeComparison, OptionChainResult, NetPnlReport, CombinedWalletReport } from "./types";

export class ArbTelegramBot {
  private bot: TelegramBot;
  private engine: ArbitrageEngine;
  private chatId: string;

  constructor() {
    this.engine = new ArbitrageEngine();
    this.chatId = Config.TELEGRAM_CHAT_ID;

    this.bot = new TelegramBot(Config.TELEGRAM_BOT_TOKEN, { polling: true });
    this.setupHandlers();
    this.setupAutoClose();
  }

  /**
   * Setup command and callback query handlers.
   */
  private setupHandlers(): void {
    // /start command
    this.bot.onText(/\/start/, (msg: TelegramBot.Message) => {
      this.sendMainMenu(msg.chat.id);
    });

    // /menu command
    this.bot.onText(/\/menu/, (msg: TelegramBot.Message) => {
      this.sendMainMenu(msg.chat.id);
    });

    // /pnl command - Live Net PnL
    this.bot.onText(/\/pnl/, async (msg: TelegramBot.Message) => {
      await this.handleShowNetPnl(msg.chat.id);
    });

    // /wallet or /balance command - Live Balances
    this.bot.onText(/\/(wallet|balance)/, async (msg: TelegramBot.Message) => {
      await this.handleShowWallet(msg.chat.id);
    });

    // /close command - Close all positions
    this.bot.onText(/\/close/, async (msg: TelegramBot.Message) => {
      await this.handleCloseAll(msg.chat.id);
    });

    // /positions command
    this.bot.onText(/\/positions/, async (msg: TelegramBot.Message) => {
      await this.handleShowNetPnl(msg.chat.id);
    });

    // /help command
    this.bot.onText(/\/help/, (msg: TelegramBot.Message) => {
      this.showHelp(msg.chat.id);
    });

    // Text messages handler
    this.bot.on("message", async (msg: TelegramBot.Message) => {
      if (!msg.text || msg.text.startsWith("/")) return;
      const text = msg.text.trim().toLowerCase();

      if (text.includes("pnl") || text.includes("profit") || text.includes("net")) {
        await this.handleShowNetPnl(msg.chat.id);
      } else if (text.includes("wallet") || text.includes("balance")) {
        await this.handleShowWallet(msg.chat.id);
      } else if (text === "close" || text === "close all") {
        await this.handleCloseAll(msg.chat.id);
      } else if (text.includes("btc")) {
        await this.showExpirySelection(msg.chat.id, "BTC");
      } else if (text.includes("eth")) {
        await this.showExpirySelection(msg.chat.id, "ETH");
      } else if (text.includes("xaut") || text.includes("gold")) {
        await this.showExpirySelection(msg.chat.id, "XAUTUSDT");
      } else if (text.includes("menu") || text.includes("scan") || text.includes("options")) {
        this.sendMainMenu(msg.chat.id);
      } else {
        this.sendMainMenu(msg.chat.id);
      }
    });

    // Inline button callback query handler
    this.bot.on("callback_query", async (query: TelegramBot.CallbackQuery) => {
      if (!query.data || !query.message) return;
      const chatId = query.message.chat.id;
      const data = query.data;

      try {
        if (data.startsWith("asset_")) {
          const assetKey = data.replace("asset_", "");
          await this.bot.answerCallbackQuery(query.id, { text: `Selected ${assetKey}` });
          await this.showExpirySelection(chatId, assetKey);
        } else if (data.startsWith("expiry_")) {
          // Format: expiry_BTC_2026-11-04
          const parts = data.replace("expiry_", "").split("_");
          const assetKey = parts[0];
          const expiryDate = parts.slice(1).join("-");
          await this.bot.answerCallbackQuery(query.id, { text: `Scanning ${assetKey} options...` });
          await this.handleScanOptions(chatId, assetKey, expiryDate);
        } else if (data.startsWith("exec_")) {
          // Format: exec_BTC_96000_CALL_2026-11-04
          await this.bot.answerCallbackQuery(query.id, { text: "⚡ Triggering Live Orders..." });
          await this.handleExecuteTrade(chatId, data);
        } else if (data === "view_pnl") {
          await this.bot.answerCallbackQuery(query.id, { text: "📊 Fetching Live Net PnL..." });
          await this.handleShowNetPnl(chatId);
        } else if (data === "view_wallet") {
          await this.bot.answerCallbackQuery(query.id, { text: "💼 Fetching Live Wallets..." });
          await this.handleShowWallet(chatId);
        } else if (data === "close_all_confirm") {
          await this.bot.answerCallbackQuery(query.id, { text: "🛑 Closing ALL open positions..." });
          await this.handleCloseAll(chatId);
        } else if (data === "main_menu") {
          await this.bot.answerCallbackQuery(query.id);
          this.sendMainMenu(chatId);
        } else {
          await this.bot.answerCallbackQuery(query.id);
        }
      } catch (err: any) {
        console.error(`Callback error: ${err.message}`);
        await this.bot.answerCallbackQuery(query.id, { text: `Error: ${err.message}` });
        await this.sendMsg(chatId, `❌ Error: ${err.message}`);
      }
    });
  }

  /**
   * Schedule automatic position close at 1:30 PM IST daily.
   * 1:30 PM IST = 08:00 UTC.
   */
  private setupAutoClose(): void {
    cron.schedule("0 8 * * *", async () => {
      console.log("⏰ 1:30 PM IST CUTOFF: Auto-closing all open positions across Delta & CoinDCX...");
      await this.autoCloseAllPositions();
    }, { timezone: "UTC" });

    console.log("✅ Auto-close scheduled: 1:30 PM IST (08:00 UTC) every day");
  }

  /**
   * Auto-close all positions and notify via Telegram.
   */
  private async autoCloseAllPositions(): Promise<void> {
    const chatId = this.chatId;
    if (!chatId) return;

    try {
      await this.sendMsg(
        chatId,
        "🚨 *AUTO-CLOSE 1:30 PM IST* 🚨\n" +
        "━━━━━━━━━━━━━━━━━━━━━━\n" +
        "⏰ Daily cutoff reached! Executing market orders to close ALL positions on Delta & CoinDCX..."
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
        lines.push("   • No active positions found.");
      }

      lines.push("");
      lines.push(`🏦 *CoinDCX:* ${result.coindcx.closedCount} position(s) closed`);
      if (result.coindcx.details.length > 0) {
        lines.push(...result.coindcx.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No active positions found.");
      }

      lines.push("");
      lines.push("✅ Both legs flat! Ready for next trade cycle.");
      lines.push("👉 Click /start to scan next-day opportunities.");

      await this.sendMsg(chatId, lines.join("\n"));
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Auto-close error: ${err.message}`);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // UI SCREENS & INTERACTIVE BUTTONS
  // ─────────────────────────────────────────────────────────────

  /**
   * Main Menu: Asset selection, Wallet, Net PnL, Close All.
   */
  public sendMainMenu(chatId: number | string): void {
    const keyboard: TelegramBot.InlineKeyboardButton[][] = [
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
      "🏛 *Delta Exchange:* LONG (BUY 10 lots = 0.1)\n" +
      "🏦 *CoinDCX:* SHORT (SELL 1 lot = 0.1)\n\n" +
      "🎯 *Strategy:* Short CoinDCX (higher premium) & Long Delta (lower premium)\n" +
      "⏰ *Auto-Close:* 1:30 PM IST daily\n\n" +
      "Select an action or asset below:";

    this.sendMsg(chatId, text, { reply_markup: { inline_keyboard: keyboard } });
  }

  /**
   * Expiry Selection: Today's Expiry vs Tomorrow's Expiry.
   */
  private async showExpirySelection(chatId: number | string, assetKey: string): Promise<void> {
    const { today, tomorrow } = this.engine.getExpiryDates();
    const asset = ASSETS[assetKey];

    const keyboard: TelegramBot.InlineKeyboardButton[][] = [
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
      `📦 *Contract Sizing:*\n` +
      `   • CoinDCX: *${asset.coindcxLots} lot* = ${asset.lotSizeUnits} ${asset.symbol}\n` +
      `   • Delta: *${asset.deltaLots} lots* = ${asset.lotSizeUnits} ${asset.symbol}\n\n` +
      `Choose expiry date:`;

    await this.sendMsg(chatId, text, { reply_markup: { inline_keyboard: keyboard } });
  }

  /**
   * Options Scanner Screen: Lists ATM & OTM Strikes with individual Execute buttons.
   */
  private async handleScanOptions(
    chatId: number | string,
    assetKey: string,
    expiryDate: string
  ): Promise<void> {
    const asset = ASSETS[assetKey];

    await this.sendMsg(
      chatId,
      `🔍 *Scanning ${asset.symbol} live options for ${expiryDate}...*\n` +
      `_Connecting to Delta Exchange and CoinDCX..._`
    );

    try {
      const results = await this.engine.scanOptions(assetKey, expiryDate);

      if (results.length === 0 || results[0].allStrikes.length === 0) {
        await this.sendMsg(
          chatId,
          `⚠️ No active options contracts found for *${asset.symbol}* expiring *${expiryDate}*.\n` +
          `Try selecting another expiry or checking market hours.`
        );
        return;
      }

      const chain = results[0];
      const spotStr = chain.spotPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const spotInr = Math.round(chain.spotPrice * Config.USD_INR_RATE).toLocaleString("en-IN");

      let text = `📊 *${asset.symbol} OPTIONS ARBITRAGE (LIVE)*\n`;
      text += `━━━━━━━━━━━━━━━━━━━━━━\n`;
      text += `🪙 *Spot:* *$${spotStr}* (₹${spotInr})\n`;
      text += `📅 *Expiry:* *${chain.expiryDate}*\n`;
      text += `📦 *Trade Size:* *${asset.lotSizeUnits} ${asset.symbol}* (Delta: 10 lots | CoinDCX: 1 lot)\n`;
      text += `━━━━━━━━━━━━━━━━━━━━━━\n\n`;

      // ── Suggested Trades (Short Premium >= Long Premium) ──
      if (chain.suggestedTrades.length > 0) {
        text += `🔥 *SUGGESTED TRADES (Short Premium ≥ Long Premium)* 🔥\n`;
        text += `_CoinDCX short premium covers Delta long cost = Inherent edge:_\n\n`;
        for (const t of chain.suggestedTrades.slice(0, 4)) {
          text += this.formatStrikeRow(t, true);
        }
        text += `\n`;
      } else {
        text += `ℹ️ _No strict CoinDCX ≥ Delta spread right now. Market ATM/OTM rates below:_\n\n`;
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

      text += `👉 *Tap any button below to instantly execute the trade live:*`;

      // ── Build Clickable Execute Buttons ──
      const keyboard: TelegramBot.InlineKeyboardButton[][] = [];

      for (const s of chain.allStrikes) {
        const tag = s.isSuggested ? "🟢 ARB" : "⚪";
        const label = `${tag} ${s.strike.toLocaleString()} ${s.optionType} | Sprd: ${s.spreadUsd >= 0 ? "+" : ""}$${s.spreadUsd}`;
        const cbData = `exec_${assetKey}_${s.strike}_${s.optionType}_${s.expiryDate}`;
        keyboard.push([{ text: label, callback_data: cbData }]);
      }

      // Action buttons
      keyboard.push([
        { text: "📈 View Net PnL", callback_data: "view_pnl" },
        { text: "💼 View Wallet", callback_data: "view_wallet" },
      ]);
      keyboard.push([
        { text: "🔄 Refresh Scan", callback_data: `expiry_${assetKey}_${chain.expiryDate}` },
        { text: "🔙 Main Menu", callback_data: "main_menu" },
      ]);

      await this.sendMsg(chatId, text, { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Scan error: ${err.message}`);
    }
  }

  /**
   * Strike row formatting.
   */
  private formatStrikeRow(s: StrikeComparison, highlight = false): string {
    const icon = s.isSuggested ? "🟢" : "⚪";
    const spreadTag = s.spreadUsd >= 0 ? `+${s.spreadPct}%` : `${s.spreadPct}%`;

    let r = `${icon} *${s.strike.toLocaleString()} ${s.optionType}* (${s.category})\n`;
    r += `   • Delta (Long): *$${s.deltaPrice.toFixed(2)}* (IV: ${s.deltaIv.toFixed(1)}%)\n`;
    r += `   • CoinDCX (Short): *$${s.coindcxPrice.toFixed(2)}* (IV: ${s.coindcxIv.toFixed(1)}%)\n`;
    r += `   • Spread: *${s.spreadUsd >= 0 ? "+" : ""}$${s.spreadUsd.toFixed(2)}* (${spreadTag})\n`;
    r += `   • Lot (${s.underlyingUnits} units): *+$${s.potentialProfitUsd.toFixed(2)}* (₹${s.potentialProfitInr.toLocaleString("en-IN")})\n`;
    return r;
  }

  /**
   * Execute Trade Handler: Fired when user clicks any strike button!
   */
  private async handleExecuteTrade(chatId: number | string, callbackData: string): Promise<void> {
    // Format: exec_BTC_96000_CALL_2026-11-04
    const parts = callbackData.replace("exec_", "").split("_");
    if (parts.length < 4) {
      await this.sendMsg(chatId, "❌ Invalid execution parameters");
      return;
    }

    const assetKey = parts[0];
    const strikeNum = parseFloat(parts[1]);
    const optionType = parts[2] as "CALL" | "PUT";
    const expiryDate = parts.slice(3).join("-");

    const asset = ASSETS[assetKey];
    if (!asset) {
      await this.sendMsg(chatId, `❌ Unknown asset: ${assetKey}`);
      return;
    }

    await this.sendMsg(
      chatId,
      `⚡ *EXECUTING LIVE ARBITRAGE TRADE...*\n` +
      `━━━━━━━━━━━━━━━━━━━━━━\n` +
      `🪙 *${asset.symbol}* | Strike: *${strikeNum.toLocaleString()} ${optionType}*\n` +
      `📅 Expiry: *${expiryDate}*\n\n` +
      `🏛 *Delta Exchange:* BUY ${asset.deltaLots} lots (LONG)\n` +
      `🏦 *CoinDCX:* SELL ${asset.coindcxLots} lot(s) (SHORT)\n` +
      `📦 Total Size: *${asset.lotSizeUnits} ${asset.symbol}*\n\n` +
      `_Dispatching live signed API orders..._`
    );

    try {
      const results = await this.engine.scanOptions(assetKey, expiryDate);
      const chain = results[0];
      if (!chain) {
        await this.sendMsg(chatId, `❌ Could not refresh options pricing for ${expiryDate}`);
        return;
      }

      const strikeData = chain.allStrikes.find(
        (s) => s.strike === strikeNum && s.optionType === optionType
      );

      if (!strikeData) {
        await this.sendMsg(chatId, `❌ Strike ${strikeNum} ${optionType} not found in live books`);
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
        `📦 Lot Sizing: *${asset.lotSizeUnits} ${asset.symbol}*`,
        ``,
        `🏛 *Delta Exchange (LONG):* ${trade.deltaOrderResult?.success ? "✅ FILLED" : "❌ FAILED"}`,
        `   • Details: ${trade.deltaOrderResult?.message || "No response"}`,
        ``,
        `🏦 *CoinDCX (SHORT):* ${trade.coindcxOrderResult?.success ? "✅ FILLED" : "❌ FAILED"}`,
        `   • Details: ${trade.coindcxOrderResult?.message || "No response"}`,
      ];

      if (trade.error) {
        lines.push(`\n⚠️ *Warning:* ${trade.error}`);
      }

      lines.push(`\n⏰ *Auto-Close:* Scheduled for 1:30 PM IST daily.`);

      const keyboard: TelegramBot.InlineKeyboardButton[][] = [
        [
          { text: "📈 View Live Net PnL", callback_data: "view_pnl" },
          { text: "💼 View Wallet", callback_data: "view_wallet" },
        ],
        [
          { text: "🛑 Close All Positions", callback_data: "close_all_confirm" },
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMsg(chatId, lines.join("\n"), { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Execution failed: ${err.message}`);
    }
  }

  /**
   * Live Net PnL & Open Positions Screen.
   */
  public async handleShowNetPnl(chatId: number | string): Promise<void> {
    await this.sendMsg(chatId, "📊 *Fetching Live Net PnL & Positions from Delta & CoinDCX...*");

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

      lines.push(`\n🏦 *CoinDCX Positions:*`);
      if (pnlReport.coindcxPositions.length === 0) {
        lines.push("   • _No open positions (Flat)_");
      } else {
        for (const p of pnlReport.coindcxPositions) {
          const sign = p.unrealizedPnlUsd >= 0 ? "+" : "";
          lines.push(
            `   • *${p.symbol}*: ${p.side} ${p.size} lots\n` +
            `     Entry: $${p.entryPrice.toFixed(2)} | Mark: $${p.markPrice.toFixed(2)}\n` +
            `     PnL: *${sign}$${p.unrealizedPnlUsd.toFixed(2)}* (₹${p.unrealizedPnlInr.toLocaleString("en-IN")})`
          );
        }
        lines.push(`   👉 *CoinDCX Subtotal:* ${pnlReport.coindcxUnrealizedUsd >= 0 ? "+" : ""}$${pnlReport.coindcxUnrealizedUsd.toFixed(2)}`);
      }

      lines.push(
        `\n📊 *Status:* ${pnlReport.isFlat ? "🟢 FLAT (Ready for entry)" : "🟡 ACTIVE ARBITRAGE IN PROGRESS"}`
      );
      lines.push(`⏰ *Auto-Close:* All positions close at 1:30 PM IST.`);

      const keyboard: TelegramBot.InlineKeyboardButton[][] = [
        [
          { text: "🔄 Refresh Net PnL", callback_data: "view_pnl" },
          { text: "🛑 Close All Positions", callback_data: "close_all_confirm" },
        ],
        [
          { text: "💼 View Wallet", callback_data: "view_wallet" },
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMsg(chatId, lines.join("\n"), { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Error fetching Net PnL: ${err.message}`);
    }
  }

  /**
   * Live Wallet Balances Screen.
   */
  public async handleShowWallet(chatId: number | string): Promise<void> {
    await this.sendMsg(chatId, "💼 *Fetching Live Wallet Balances from Delta & CoinDCX...*");

    try {
      const report: CombinedWalletReport = await this.engine.getLiveWalletBalances();

      const lines = [
        `💼 *LIVE WALLET BALANCES & MARGIN*`,
        `━━━━━━━━━━━━━━━━━━━━━━`,
        `💰 *Total Equity:* *$${report.totalBalanceUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${report.totalBalanceInr.toLocaleString("en-IN")})`,
        `🛡 *Available Margin:* *$${report.totalAvailableUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${report.totalAvailableInr.toLocaleString("en-IN")})`,
        `━━━━━━━━━━━━━━━━━━━━━━\n`,
        `🏛 *Delta Exchange (Live):*`,
        `   • Total: *$${report.delta.balanceUsd.toFixed(2)}* (₹${report.delta.balanceInr.toLocaleString("en-IN")})`,
        `   • Available: *$${report.delta.availableUsd.toFixed(2)}*`,
      ];

      if (report.delta.currencies.length > 0) {
        for (const c of report.delta.currencies) {
          lines.push(`   • ${c.symbol}: \`${c.total.toFixed(4)}\` (Avail: \`${c.available.toFixed(4)}\`)`);
        }
      }
      if (report.delta.error) {
        lines.push(`   • ⚠️ Status: ${report.delta.error}`);
      }

      lines.push(`\n🏦 *CoinDCX (Live):*`);
      lines.push(
        `   • Total: *$${report.coindcx.balanceUsd.toFixed(2)}* (₹${report.coindcx.balanceInr.toLocaleString("en-IN")})`
      );
      lines.push(`   • Available: *$${report.coindcx.availableUsd.toFixed(2)}*`);

      if (report.coindcx.currencies.length > 0) {
        for (const c of report.coindcx.currencies) {
          lines.push(`   • ${c.symbol}: \`${c.total.toFixed(4)}\` (Avail: \`${c.available.toFixed(4)}\`)`);
        }
      }
      if (report.coindcx.error) {
        lines.push(`   • ⚠️ Status: ${report.coindcx.error}`);
      }

      lines.push(`\n📦 *TRADE LOT REQUIREMENT:*`);
      lines.push(`   • 1 Trade = 0.1 BTC / ETH / XAUT`);
      lines.push(`   • Delta: 10 lots | CoinDCX: 1 lot`);

      const canTrade = report.delta.availableUsd > 10 && report.coindcx.availableUsd > 10;
      if (canTrade) {
        lines.push(`   • Status: 🟢 *MARGIN READY ON BOTH EXCHANGES*`);
      } else {
        lines.push(`   • Status: ⚠️ *Ensure both accounts hold sufficient margin before trading*`);
      }

      const keyboard: TelegramBot.InlineKeyboardButton[][] = [
        [
          { text: "🔄 Refresh Wallet", callback_data: "view_wallet" },
          { text: "📈 View Net PnL", callback_data: "view_pnl" },
        ],
        [
          { text: "⚡ Trade BTC", callback_data: "asset_BTC" },
          { text: "⚡ Trade ETH", callback_data: "asset_ETH" },
          { text: "⚡ Trade XAUT", callback_data: "asset_XAUTUSDT" },
        ],
        [
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMsg(chatId, lines.join("\n"), { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Error checking wallet: ${err.message}`);
    }
  }

  /**
   * Close All Open Positions Handler.
   */
  public async handleCloseAll(chatId: number | string): Promise<void> {
    await this.sendMsg(
      chatId,
      "🚨 *CLOSING ALL POSITIONS LIVE ON DELTA & COINDCX...*\n" +
      "_Submitting market orders to flatten both exchange accounts..._"
    );

    try {
      const result = await this.engine.closeAllPositions();

      const lines = [
        "🛑 *POSITION CLOSURE COMPLETE*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        `🏛 *Delta Exchange:* Closed ${result.delta.closedCount} position(s)`,
      ];
      if (result.delta.details.length > 0) {
        lines.push(...result.delta.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No open positions found on Delta.");
      }

      lines.push("");
      lines.push(`🏦 *CoinDCX:* Closed ${result.coindcx.closedCount} position(s)`);
      if (result.coindcx.details.length > 0) {
        lines.push(...result.coindcx.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No open positions found on CoinDCX.");
      }

      lines.push("\n✅ Both legs executed! Send /pnl or tap below to verify flat state.");

      const keyboard: TelegramBot.InlineKeyboardButton[][] = [
        [
          { text: "📈 Verify Net PnL", callback_data: "view_pnl" },
          { text: "🔙 Main Menu", callback_data: "main_menu" },
        ],
      ];

      await this.sendMsg(chatId, lines.join("\n"), { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Error closing positions: ${err.message}`);
    }
  }

  /**
   * Help Message.
   */
  private showHelp(chatId: number | string): void {
    const text = [
      "🤖 *OPTIONS ARBITRAGE BOT COMMANDS*",
      "━━━━━━━━━━━━━━━━━━━━━━",
      "• /start or /menu — Open the interactive main menu",
      "• /pnl — Check Live Net PnL across Delta & CoinDCX",
      "• /wallet or /balance — View Live Wallet balances",
      "• /close — 🚨 Instantly close all open positions on both exchanges",
      "• /help — Show this help message\n",
      "⚡ *ARBITRAGE STRATEGY:*",
      "• BUY (Long) on Delta Exchange",
      "• SELL (Short) on CoinDCX",
      "• Edge: Short Premium ≥ Long Premium\n",
      "📦 *LOT SIZING:*",
      "• CoinDCX: 1 lot = 0.1 BTC / ETH / XAUT",
      "• Delta: 10 lots = 0.1 BTC / ETH / XAUT\n",
      "⏰ *AUTO-CLOSE:* 1:30 PM IST daily",
    ].join("\n");

    this.sendMsg(chatId, text);
  }

  // ─────────────────────────────────────────────────────────────
  // UTILITY METHODS
  // ─────────────────────────────────────────────────────────────

  /**
   * Send message with automatic chunking.
   */
  private async sendMsg(
    chatId: number | string,
    text: string,
    opts?: TelegramBot.SendMessageOptions
  ): Promise<void> {
    const chunkSize = 3900;

    for (let i = 0; i < text.length; i += chunkSize) {
      const chunk = text.substring(i, i + chunkSize);
      const isLast = i + chunkSize >= text.length;

      try {
        await this.bot.sendMessage(chatId, chunk, {
          parse_mode: "Markdown",
          ...(isLast ? opts : {}),
        });
      } catch {
        try {
          await this.bot.sendMessage(chatId, chunk, {
            ...(isLast ? opts : {}),
          });
        } catch (retryErr: any) {
          console.error(`Telegram send failure: ${retryErr.message}`);
        }
      }
    }
  }

  public start(): void {
    console.log("🤖 Telegram Bot polling started!");
    console.log("⏰ 1:30 PM IST Auto-Close active.");
    if (this.chatId) {
      this.sendMainMenu(this.chatId);
    }
  }

  public stop(): void {
    this.bot.stopPolling();
    console.log("Telegram Bot polling stopped.");
  }
}
