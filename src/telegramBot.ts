/**
 * Interactive Telegram Bot for Options Arbitrage.
 *
 * Flow:
 * 1. /start → Select Asset (BTC, ETH, XAUTUSDT)
 * 2. → Select Expiry (Today / Tomorrow)
 * 3. → Show ATM + OTM options for Calls and Puts with prices from both exchanges
 * 4. → Execute button: Delta Long + CoinDCX Short
 * 5. → Auto-close all positions at 1:30 PM IST daily
 *
 * Strategy: Short CoinDCX (higher premium) + Long Delta (lower premium)
 * Lot sizes: CoinDCX 1 lot = 0.1 unit | Delta 10 lots = 0.1 unit
 */

import TelegramBot from "node-telegram-bot-api";
import cron from "node-cron";
import { Config, ASSETS, AssetConfig } from "./config";
import { ArbitrageEngine } from "./arbitrageEngine";
import { StrikeComparison, OptionChainResult } from "./types";

export class ArbTelegramBot {
  private bot: TelegramBot;
  private engine: ArbitrageEngine;
  private chatId: string;
  private autoCloseScheduled = false;

  constructor() {
    this.engine = new ArbitrageEngine();
    this.chatId = Config.TELEGRAM_CHAT_ID;

    this.bot = new TelegramBot(Config.TELEGRAM_BOT_TOKEN, { polling: true });
    this.setupHandlers();
    this.setupAutoClose();
  }

  /**
   * Setup all bot command and callback handlers.
   */
  private setupHandlers(): void {
    // /start command
    this.bot.onText(/\/start/, (msg) => {
      this.showAssetSelection(msg.chat.id);
    });

    // /scan or /options command
    this.bot.onText(/\/(scan|options)/, (msg) => {
      this.showAssetSelection(msg.chat.id);
    });

    // /close command - close all positions
    this.bot.onText(/\/close/, async (msg) => {
      await this.handleCloseAll(msg.chat.id);
    });

    // /positions command - show current positions
    this.bot.onText(/\/positions/, async (msg) => {
      await this.handleShowPositions(msg.chat.id);
    });

    // /help command
    this.bot.onText(/\/help/, (msg) => {
      this.showHelp(msg.chat.id);
    });

    // Text commands
    this.bot.on("message", (msg) => {
      if (!msg.text || msg.text.startsWith("/")) return;
      const text = msg.text.toLowerCase().trim();

      if (text === "scan" || text === "options") {
        this.showAssetSelection(msg.chat.id);
      } else if (text === "close") {
        this.handleCloseAll(msg.chat.id);
      } else if (text === "positions" || text === "current") {
        this.handleShowPositions(msg.chat.id);
      } else if (text === "help") {
        this.showHelp(msg.chat.id);
      }
    });

    // Callback query handler for inline buttons
    this.bot.on("callback_query", async (query) => {
      if (!query.data || !query.message) return;

      const chatId = query.message.chat.id;
      const data = query.data;

      try {
        if (data.startsWith("asset_")) {
          // Asset selected → show expiry selection
          const assetKey = data.replace("asset_", "");
          await this.bot.answerCallbackQuery(query.id, { text: `Selected ${assetKey}` });
          await this.showExpirySelection(chatId, assetKey);
        } else if (data.startsWith("expiry_")) {
          // Expiry selected → scan and show options
          const parts = data.replace("expiry_", "").split("_");
          const assetKey = parts[0];
          const expiry = parts.slice(1).join("_"); // "today" or "tomorrow" or date
          await this.bot.answerCallbackQuery(query.id, { text: "Scanning options..." });
          await this.handleScanOptions(chatId, assetKey, expiry);
        } else if (data.startsWith("execute_")) {
          // Execute trade
          await this.bot.answerCallbackQuery(query.id, { text: "⚡ Executing trade..." });
          await this.handleExecuteTrade(chatId, data);
        } else if (data === "close_all") {
          await this.bot.answerCallbackQuery(query.id, { text: "🛑 Closing all positions..." });
          await this.handleCloseAll(chatId);
        } else if (data === "refresh") {
          await this.bot.answerCallbackQuery(query.id, { text: "Refreshing..." });
          await this.showAssetSelection(chatId);
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
   * Schedule auto-close at 1:30 PM IST daily.
   * 1:30 PM IST = 08:00 UTC
   */
  private setupAutoClose(): void {
    // Cron: minute hour * * * (UTC)
    // 1:30 PM IST = 08:00 UTC
    cron.schedule("0 8 * * *", async () => {
      console.log("⏰ AUTO-CLOSE triggered at 1:30 PM IST");
      await this.autoCloseAllPositions();
    }, { timezone: "UTC" });

    console.log("✅ Auto-close scheduled for 1:30 PM IST (08:00 UTC) daily");
  }

  /**
   * Auto-close all positions and notify.
   */
  private async autoCloseAllPositions(): Promise<void> {
    const chatId = this.chatId;
    try {
      await this.sendMsg(chatId,
        "🚨 *AUTO-CLOSE 1:30 PM IST* 🚨\n" +
        "━━━━━━━━━━━━━━━━━━━━━━\n" +
        "⏰ Closing ALL positions on both exchanges..."
      );

      const result = await this.engine.closeAllPositions();

      const lines = [
        "🛑 *AUTO-CLOSE COMPLETE* 🛑",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        `🏛 *Delta Exchange:* Closed ${result.delta.closedCount} position(s)`,
      ];

      if (result.delta.details.length > 0) {
        lines.push(...result.delta.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No active positions found");
      }

      lines.push("");
      lines.push(`🏦 *CoinDCX:* Closed ${result.coindcx.closedCount} position(s)`);

      if (result.coindcx.details.length > 0) {
        lines.push(...result.coindcx.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No active positions found");
      }

      lines.push("");
      lines.push("✅ All positions closed! Ready for next trade cycle.");
      lines.push("👉 Use /start to scan new opportunities");

      await this.sendMsg(chatId, lines.join("\n"));
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Auto-close error: ${err.message}`);
    }
  }

  // ─── UI Screens ───

  /**
   * Step 1: Show asset selection keyboard.
   */
  private async showAssetSelection(chatId: number | string): Promise<void> {
    const keyboard: TelegramBot.InlineKeyboardButton[][] = [
      [
        { text: "₿ BTC", callback_data: "asset_BTC" },
        { text: "⟠ ETH", callback_data: "asset_ETH" },
        { text: "🥇 XAUTUSDT", callback_data: "asset_XAUTUSDT" },
      ],
    ];

    await this.sendMsg(chatId,
      "⚡ *OPTIONS ARBITRAGE BOT* ⚡\n" +
      "━━━━━━━━━━━━━━━━━━━━━━\n" +
      "🏛 Delta Exchange (Long) ↔ 🏦 CoinDCX (Short)\n\n" +
      "Select the asset to trade:",
      { reply_markup: { inline_keyboard: keyboard } }
    );
  }

  /**
   * Step 2: Show expiry selection (Today / Tomorrow).
   */
  private async showExpirySelection(chatId: number | string, assetKey: string): Promise<void> {
    const { today, tomorrow } = this.engine.getExpiryDates();

    const keyboard: TelegramBot.InlineKeyboardButton[][] = [
      [
        { text: `📅 Today (${today})`, callback_data: `expiry_${assetKey}_${today}` },
        { text: `📅 Tomorrow (${tomorrow})`, callback_data: `expiry_${assetKey}_${tomorrow}` },
      ],
      [
        { text: "📅 Both Days", callback_data: `expiry_${assetKey}_both` },
      ],
      [
        { text: "🔙 Back", callback_data: "refresh" },
      ],
    ];

    const asset = ASSETS[assetKey];
    await this.sendMsg(chatId,
      `🪙 *${asset.name} (${asset.symbol})* selected\n\n` +
      `📦 *Lot Sizing:*\n` +
      `   • CoinDCX: ${asset.coindcxLots} lot = ${asset.lotSizeUnits} ${asset.symbol}\n` +
      `   • Delta: ${asset.deltaLots} lots = ${asset.lotSizeUnits} ${asset.symbol}\n\n` +
      `Select expiry:`,
      { reply_markup: { inline_keyboard: keyboard } }
    );
  }

  /**
   * Step 3: Scan and show options chain with execute buttons.
   */
  private async handleScanOptions(chatId: number | string, assetKey: string, expiry: string): Promise<void> {
    const asset = ASSETS[assetKey];

    await this.sendMsg(chatId,
      `🔍 *Scanning ${asset.symbol} options on Delta & CoinDCX...*\n` +
      `_This may take a few seconds..._`
    );

    try {
      let expiryFilter: string | undefined;
      if (expiry !== "both") {
        expiryFilter = expiry;
      }

      const results = await this.engine.scanOptions(assetKey, expiryFilter);

      if (results.length === 0) {
        await this.sendMsg(chatId,
          `⚠️ No options data found for ${asset.symbol} on the selected expiry.\n` +
          `Try another date or check if markets are open.`
        );
        return;
      }

      for (const chainResult of results) {
        await this.sendOptionChainMessage(chatId, assetKey, chainResult);
      }
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Scan error: ${err.message}`);
    }
  }

  /**
   * Format and send option chain with execute buttons.
   */
  private async sendOptionChainMessage(
    chatId: number | string,
    assetKey: string,
    chain: OptionChainResult
  ): Promise<void> {
    const asset = ASSETS[assetKey];
    const spotStr = chain.spotPrice.toLocaleString("en-US", { maximumFractionDigits: 2 });

    // ── Header ──
    let msg = "";
    msg += `📊 *${asset.symbol} OPTIONS — Expiry: ${chain.expiryDate}*\n`;
    msg += `━━━━━━━━━━━━━━━━━━━━━━\n`;
    msg += `🪙 Spot: *$${spotStr}* | ATM Strike: *${chain.atmStrike.toLocaleString()}*\n`;
    msg += `🏛 Delta (Long) ↔ 🏦 CoinDCX (Short)\n\n`;

    // ── Suggested Trades (where CoinDCX >= Delta) ──
    if (chain.suggestedTrades.length > 0) {
      msg += `🔥 *SUGGESTED TRADES (Short ≥ Long Premium)* 🔥\n`;
      msg += `_CoinDCX premium ≥ Delta premium = Profitable arb_\n\n`;
      for (const t of chain.suggestedTrades.slice(0, 5)) {
        msg += this.formatStrikeRow(t, true);
      }
      msg += `\n`;
    } else {
      msg += `ℹ️ _No profitable arb right now (CoinDCX < Delta on all strikes)_\n\n`;
    }

    // ── ATM Options ──
    msg += `📍 *ATM STRIKES (${chain.atmStrike.toLocaleString()})*\n`;
    msg += `─────────────────────\n`;
    for (const c of chain.atmCalls) {
      msg += this.formatStrikeRow(c);
    }
    for (const p of chain.atmPuts) {
      msg += this.formatStrikeRow(p);
    }
    msg += `\n`;

    // ── OTM Calls ──
    if (chain.otmCalls.length > 0) {
      msg += `📈 *OTM CALLS (Above Spot)*\n`;
      msg += `─────────────────────\n`;
      for (const c of chain.otmCalls) {
        msg += this.formatStrikeRow(c);
      }
      msg += `\n`;
    }

    // ── OTM Puts ──
    if (chain.otmPuts.length > 0) {
      msg += `📉 *OTM PUTS (Below Spot)*\n`;
      msg += `─────────────────────\n`;
      for (const p of chain.otmPuts) {
        msg += this.formatStrikeRow(p);
      }
      msg += `\n`;
    }

    // ── Build execute buttons ──
    const keyboard: TelegramBot.InlineKeyboardButton[][] = [];

    // Add buttons for all strikes
    const allStrikes = chain.allStrikes;
    for (let i = 0; i < allStrikes.length; i += 2) {
      const row: TelegramBot.InlineKeyboardButton[] = [];
      for (let j = i; j < Math.min(i + 2, allStrikes.length); j++) {
        const s = allStrikes[j];
        const tag = s.isSuggested ? "✅" : "⚪";
        const label = `${tag} ${s.strike} ${s.optionType}`;
        const cbData = `execute_${assetKey}_${s.strike}_${s.optionType}_${s.expiryDate}`;
        row.push({ text: label, callback_data: cbData });
      }
      keyboard.push(row);
    }

    // Add utility buttons
    keyboard.push([
      { text: "🔄 Refresh", callback_data: `expiry_${assetKey}_${chain.expiryDate}` },
      { text: "🛑 Close All", callback_data: "close_all" },
    ]);
    keyboard.push([
      { text: "🔙 Change Asset", callback_data: "refresh" },
    ]);

    await this.sendMsg(chatId, msg, { reply_markup: { inline_keyboard: keyboard } });
  }

  /**
   * Format a single strike comparison row.
   */
  private formatStrikeRow(s: StrikeComparison, highlight = false): string {
    const tag = s.isSuggested ? "🟢" : "⚪";
    const arrow = highlight ? "➡️" : "•";

    let row = `${tag} *${s.strike.toLocaleString()} ${s.optionType}* (${s.category})\n`;
    row += `   ${arrow} Delta (Long):  $${s.deltaPrice.toFixed(2)} | IV: ${s.deltaIv.toFixed(1)}%\n`;
    row += `   ${arrow} CoinDCX (Short): $${s.coindcxPrice.toFixed(2)} | IV: ${s.coindcxIv.toFixed(1)}%\n`;
    row += `   ${arrow} Spread: ${s.spreadUsd >= 0 ? "+" : ""}$${s.spreadUsd.toFixed(2)} (${s.spreadPct >= 0 ? "+" : ""}${s.spreadPct.toFixed(1)}%)\n`;

    return row;
  }

  /**
   * Handle trade execution from button press.
   */
  private async handleExecuteTrade(chatId: number | string, callbackData: string): Promise<void> {
    // Parse: execute_BTC_95000_CALL_2026-11-04
    const parts = callbackData.replace("execute_", "").split("_");
    if (parts.length < 4) {
      await this.sendMsg(chatId, "❌ Invalid trade data");
      return;
    }

    const assetKey = parts[0];
    const strike = parseFloat(parts[1]);
    const optionType = parts[2] as "CALL" | "PUT";
    const expiryDate = parts.slice(3).join("-");

    const asset = ASSETS[assetKey];
    if (!asset) {
      await this.sendMsg(chatId, `❌ Unknown asset: ${assetKey}`);
      return;
    }

    // Confirm trade
    await this.sendMsg(chatId,
      `⚡ *EXECUTING ARBITRAGE TRADE* ⚡\n` +
      `━━━━━━━━━━━━━━━━━━━━━━\n` +
      `🪙 *${asset.symbol}* | Strike: *${strike.toLocaleString()}* ${optionType}\n` +
      `📅 Expiry: ${expiryDate}\n\n` +
      `🏛 *Delta Exchange:* BUY ${asset.deltaLots} lots (Long)\n` +
      `🏦 *CoinDCX:* SELL ${asset.coindcxLots} lot(s) (Short)\n` +
      `📦 Total size: ${asset.lotSizeUnits} ${asset.symbol}\n\n` +
      `_Placing orders on both exchanges..._`
    );

    try {
      // First scan to get the fresh strike data
      const results = await this.engine.scanOptions(assetKey, expiryDate);
      const chain = results[0];
      if (!chain) {
        await this.sendMsg(chatId, `❌ Could not find options data for this expiry`);
        return;
      }

      const strikeData = chain.allStrikes.find(
        (s) => s.strike === strike && s.optionType === optionType
      );

      if (!strikeData) {
        await this.sendMsg(chatId, `❌ Strike ${strike} ${optionType} not found in current data`);
        return;
      }

      if (!strikeData.deltaSymbol || !strikeData.deltaProductId) {
        await this.sendMsg(chatId, `❌ Delta product not found for this strike. Cannot execute.`);
        return;
      }

      if (!strikeData.coindcxSymbol) {
        await this.sendMsg(chatId, `❌ CoinDCX instrument not found for this strike. Cannot execute.`);
        return;
      }

      // Execute the trade
      const trade = await this.engine.executeTrade(assetKey, strikeData);

      // Report result
      const statusEmoji = trade.status === "filled" ? "✅" : trade.status === "partial" ? "⚠️" : "❌";
      const lines = [
        `${statusEmoji} *TRADE ${trade.status.toUpperCase()}* — ${trade.id}`,
        `━━━━━━━━━━━━━━━━━━━━━━`,
        `🪙 *${asset.symbol}* | ${strike.toLocaleString()} ${optionType}`,
        `📅 Expiry: ${expiryDate}`,
        ``,
        `🏛 *Delta (Long):* ${trade.deltaOrderResult?.success ? "✅ Filled" : "❌ Failed"}`,
        `   • ${trade.deltaOrderResult?.message || "No response"}`,
        ``,
        `🏦 *CoinDCX (Short):* ${trade.coindcxOrderResult?.success ? "✅ Filled" : "❌ Failed"}`,
        `   • ${trade.coindcxOrderResult?.message || "No response"}`,
      ];

      if (trade.error) {
        lines.push(`\n⚠️ *Error:* ${trade.error}`);
      }

      lines.push(`\n⏰ Auto-close: 1:30 PM IST`);
      lines.push(`👉 /close to manually close all positions`);

      const keyboard: TelegramBot.InlineKeyboardButton[][] = [
        [
          { text: "📊 View Positions", callback_data: "positions" },
          { text: "🛑 Close All", callback_data: "close_all" },
        ],
        [
          { text: "🔄 New Trade", callback_data: "refresh" },
        ],
      ];

      await this.sendMsg(chatId, lines.join("\n"), { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Trade execution error: ${err.message}`);
    }
  }

  /**
   * Close all positions on both exchanges.
   */
  private async handleCloseAll(chatId: number | string): Promise<void> {
    await this.sendMsg(chatId,
      "🚨 *CLOSING ALL POSITIONS* 🚨\n" +
      "━━━━━━━━━━━━━━━━━━━━━━\n" +
      "_Sending close orders to Delta & CoinDCX..._"
    );

    try {
      const result = await this.engine.closeAllPositions();

      const lines = [
        "🛑 *POSITIONS CLOSED* 🛑",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        `🏛 *Delta Exchange:* ${result.delta.closedCount} position(s) closed`,
      ];

      if (result.delta.details.length > 0) {
        lines.push(...result.delta.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No active positions found");
      }

      lines.push("");
      lines.push(`🏦 *CoinDCX:* ${result.coindcx.closedCount} position(s) closed`);

      if (result.coindcx.details.length > 0) {
        lines.push(...result.coindcx.details.map((d) => `   • ${d}`));
      } else {
        lines.push("   • No active positions found");
      }

      lines.push("\n✅ All done! Use /start to begin a new trade.");

      await this.sendMsg(chatId, lines.join("\n"));
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Close error: ${err.message}`);
    }
  }

  /**
   * Show current positions on both exchanges.
   */
  private async handleShowPositions(chatId: number | string): Promise<void> {
    await this.sendMsg(chatId, "📊 *Fetching positions from both exchanges...*");

    try {
      const [deltaPos, coindcxPos] = await Promise.all([
        this.engine.deltaClient.getOpenPositions(),
        this.engine.coindcxClient.getOpenPositions(),
      ]);

      const lines = [
        "📊 *CURRENT POSITIONS*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🏛 *Delta Exchange:*",
      ];

      if (deltaPos.length === 0) {
        lines.push("   • _No open positions (FLAT)_");
      } else {
        for (const p of deltaPos) {
          const sym = p.product_symbol || p.symbol;
          const size = p.size;
          const entry = p.entry_price || "—";
          const pnl = p.unrealized_pnl || "—";
          lines.push(`   • *${sym}*: Size ${size} | Entry $${entry} | PnL ${pnl}`);
        }
      }

      lines.push("");
      lines.push("🏦 *CoinDCX:*");

      if (coindcxPos.length === 0) {
        lines.push("   • _No open positions (FLAT)_");
      } else {
        for (const p of coindcxPos) {
          const sym = p.pair || p.symbol;
          const qty = p.quantity || p.size;
          const side = p.side || "—";
          lines.push(`   • *${sym}*: ${side.toUpperCase()} ${qty} | PnL ${p.unrealized_pnl || "—"}`);
        }
      }

      const isFlat = deltaPos.length === 0 && coindcxPos.length === 0;
      lines.push(`\n📈 Status: ${isFlat ? "🟢 FLAT" : "🟡 ACTIVE POSITIONS"}`);
      lines.push(`⏰ Auto-close: 1:30 PM IST`);

      const keyboard: TelegramBot.InlineKeyboardButton[][] = [
        [
          { text: "🔄 Refresh", callback_data: "positions" },
          { text: "🛑 Close All", callback_data: "close_all" },
        ],
        [
          { text: "🔙 New Trade", callback_data: "refresh" },
        ],
      ];

      await this.sendMsg(chatId, lines.join("\n"), { reply_markup: { inline_keyboard: keyboard } });
    } catch (err: any) {
      await this.sendMsg(chatId, `❌ Error fetching positions: ${err.message}`);
    }
  }

  /**
   * Show help.
   */
  private showHelp(chatId: number | string): void {
    const msg = [
      "🤖 *OPTIONS ARBITRAGE BOT* — Help",
      "━━━━━━━━━━━━━━━━━━━━━━",
      "",
      "📋 *Commands:*",
      "• /start — Select asset & scan options",
      "• /scan — Same as /start",
      "• /positions — View open positions",
      "• /close — Close ALL positions on both exchanges",
      "• /help — This help message",
      "",
      "⚡ *Strategy:*",
      "• BUY (Long) on Delta Exchange",
      "• SELL (Short) on CoinDCX",
      "• Short premium ≥ Long premium = Profit",
      "",
      "📦 *Lot Sizes:*",
      "• CoinDCX: 1 lot = 0.1 BTC/ETH/XAUT",
      "• Delta: 10 lots = 0.1 BTC/ETH/XAUT",
      "",
      "⏰ *Auto-Close:* 1:30 PM IST daily",
      "All positions automatically closed at 1:30 PM IST",
      "",
      "🟢 = Suggested trade (Short premium ≥ Long premium)",
      "⚪ = Not recommended (Short < Long premium)",
    ].join("\n");

    this.sendMsg(chatId, msg);
  }

  // ─── Utility ───

  /**
   * Send a message with Markdown, chunking if too long.
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
      } catch (err: any) {
        // Retry without Markdown if parsing failed
        try {
          await this.bot.sendMessage(chatId, chunk, {
            ...(isLast ? opts : {}),
          });
        } catch (retryErr: any) {
          console.error(`Telegram send error: ${retryErr.message}`);
        }
      }
    }
  }

  /**
   * Start the bot.
   */
  public start(): void {
    console.log("🤖 Telegram bot started and listening...");
    console.log("📋 Commands: /start, /scan, /positions, /close, /help");
    console.log("⏰ Auto-close scheduled for 1:30 PM IST daily");

    // Send startup message
    if (this.chatId) {
      this.sendMsg(this.chatId,
        "🚀 *Options Arbitrage Bot Online!* 🚀\n" +
        "━━━━━━━━━━━━━━━━━━━━━━\n" +
        "🏛 Delta Exchange (Long) ↔ 🏦 CoinDCX (Short)\n\n" +
        "📦 *Lot Sizes:*\n" +
        "   • CoinDCX: 1 lot = 0.1 unit\n" +
        "   • Delta: 10 lots = 0.1 unit\n\n" +
        "⏰ Auto-close: 1:30 PM IST daily\n\n" +
        "👉 Use /start to begin scanning!"
      );
    }
  }

  /**
   * Stop the bot.
   */
  public stop(): void {
    this.bot.stopPolling();
    console.log("Bot stopped.");
  }
}
