"use strict";
/**
 * Interactive Telegram Bot for Live Crypto Options Arbitrage.
 *
 * Exchanges:
 *  - Delta Exchange (LONG / BUY): 10 lots = 0.1 unit
 *  - CoinDCX (SHORT / SELL): 1 lot = 0.1 unit
 *
 * Capabilities:
 *  - Interactive inline buttons to scan and execute trades live
 *  - Live Net PnL tracking for positions on both exchanges
 *  - Live wallet balances (USDT, INR, Equity, Available Margin)
 *  - Short Premium >= Long Premium suggestion engine
 *  - Daily automatic position close at 1:30 PM IST
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.ArbTelegramBot = void 0;
const axios_1 = __importDefault(require("axios"));
const node_cron_1 = __importDefault(require("node-cron"));
const config_1 = require("./config");
const arbitrageEngine_1 = require("./arbitrageEngine");
class ArbTelegramBot {
    token;
    defaultChatId;
    apiUrl;
    http;
    engine;
    isRunning = false;
    lastUpdateId = 0;
    constructor(engine, token, defaultChatId) {
        this.token = token || config_1.Config.TELEGRAM_BOT_TOKEN;
        this.defaultChatId = defaultChatId || config_1.Config.TELEGRAM_CHAT_ID;
        this.apiUrl = this.token ? `https://api.telegram.org/bot${this.token}` : "";
        this.http = axios_1.default.create({ timeout: 30000 });
        this.engine = engine || new arbitrageEngine_1.ArbitrageEngine();
        this.setupAutoClose();
    }
    /**
     * Schedule automatic position close at 1:30 PM IST daily (08:00 UTC).
     */
    setupAutoClose() {
        node_cron_1.default.schedule("0 8 * * *", async () => {
            console.log("⏰ 1:30 PM IST CUTOFF: Executing automated close of all open positions...");
            await this.autoCloseAllPositions();
        }, { timezone: "UTC" });
        console.log("✅ Auto-close scheduled: 1:30 PM IST (08:00 UTC) every day");
    }
    /**
     * Auto-close all positions and notify via Telegram.
     */
    async autoCloseAllPositions() {
        const chatId = this.defaultChatId;
        if (!chatId)
            return;
        try {
            await this.sendMessage("🚨 *AUTO-CLOSE 1:30 PM IST* 🚨\n" +
                "━━━━━━━━━━━━━━━━━━━━━━\n" +
                "⏰ Daily 1:30 PM cutoff reached! Closing ALL open positions across Delta Exchange & CoinDCX...", chatId);
            const result = await this.engine.closeAllPositions();
            const lines = [
                "🛑 *DAILY 1:30 PM IST AUTO-CLOSE REPORT*",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
                `🏛 *Delta Exchange:* ${result.delta.closedCount} position(s) closed`,
            ];
            if (result.delta.details.length > 0) {
                lines.push(...result.delta.details.map((d) => `   • ${d}`));
            }
            else {
                lines.push("   • No open positions on Delta.");
            }
            lines.push("");
            lines.push(`🏦 *CoinDCX:* ${result.coindcx.closedCount} position(s) closed`);
            if (result.coindcx.details.length > 0) {
                lines.push(...result.coindcx.details.map((d) => `   • ${d}`));
            }
            else {
                lines.push("   • No open positions on CoinDCX.");
            }
            lines.push("\n✅ Both legs flat! Ready for next trade cycle.");
            lines.push("👉 Click /start to scan next-day arbitrage.");
            await this.sendMessage(lines.join("\n"), chatId);
        }
        catch (err) {
            await this.sendMessage(`❌ Auto-close error: ${err.message}`, chatId);
        }
    }
    /**
     * Send a formatted Telegram message with chunking and optional inline keyboard.
     */
    async sendMessage(text, chatId, inlineKeyboard) {
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
            const payload = {
                chat_id: targetChat,
                text: chunk,
                parse_mode: "Markdown",
            };
            if (isLastChunk && inlineKeyboard && inlineKeyboard.length > 0) {
                payload.reply_markup = { inline_keyboard: inlineKeyboard };
            }
            try {
                await this.http.post(`${this.apiUrl}/sendMessage`, payload);
            }
            catch (err) {
                // Retry without Markdown if syntax failed
                try {
                    delete payload.parse_mode;
                    await this.http.post(`${this.apiUrl}/sendMessage`, payload);
                }
                catch (retryErr) {
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
    async answerCallbackQuery(callbackQueryId, text) {
        if (!this.apiUrl)
            return;
        try {
            await this.http.post(`${this.apiUrl}/answerCallbackQuery`, {
                callback_query_id: callbackQueryId,
                text: text || "",
                show_alert: false,
            });
        }
        catch {
            // Ignore callback query ack errors
        }
    }
    // ─────────────────────────────────────────────────────────────
    // UI SCREENS & INTERACTIVE BUTTONS
    // ─────────────────────────────────────────────────────────────
    /**
     * Main Menu: Asset selection, Wallet, Net PnL, Close All.
     */
    async sendMainMenu(chatId) {
        const keyboard = [
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
        const text = "⚡ *OPTIONS ARBITRAGE BOT (LIVE)* ⚡\n" +
            "━━━━━━━━━━━━━━━━━━━━━━\n" +
            "🏛 *Delta Exchange:* LONG (BUY 10 lots = 0.1)\n" +
            "🏦 *CoinDCX:* SHORT (SELL 1 lot = 0.1)\n\n" +
            "🎯 *Strategy:* Short CoinDCX (higher premium) & Long Delta (lower premium)\n" +
            "⏰ *Auto-Close:* 1:30 PM IST daily\n\n" +
            "Select an asset to scan or choose an action below:";
        await this.sendMessage(text, chatId, keyboard);
    }
    /**
     * Expiry Selection: Today's Expiry vs Tomorrow's Expiry.
     */
    async showExpirySelection(chatId, assetKey) {
        const { today, tomorrow } = this.engine.getExpiryDates();
        const asset = config_1.ASSETS[assetKey];
        const keyboard = [
            [
                { text: `📅 Today (${today})`, callback_data: `expiry_${assetKey}_${today}` },
                { text: `📅 Tomorrow (${tomorrow})`, callback_data: `expiry_${assetKey}_${tomorrow}` },
            ],
            [
                { text: "🔙 Main Menu", callback_data: "main_menu" },
            ],
        ];
        const text = `🪙 *${asset.name} (${asset.symbol}) Options*\n` +
            `━━━━━━━━━━━━━━━━━━━━━━\n` +
            `📦 *Contract Sizing (0.1 units):*\n` +
            `   • CoinDCX: *${asset.coindcxLots} lot* = ${asset.lotSizeUnits} ${asset.symbol}\n` +
            `   • Delta: *${asset.deltaLots} lots* = ${asset.lotSizeUnits} ${asset.symbol}\n\n` +
            `Select expiry date to scan:`;
        await this.sendMessage(text, chatId, keyboard);
    }
    /**
     * Options Scanner Screen: Lists ATM & OTM Strikes with individual Execute buttons.
     */
    async handleScanOptions(chatId, assetKey, expiryDate) {
        const asset = config_1.ASSETS[assetKey];
        await this.sendMessage(`🔍 *Scanning ${asset.symbol} live options for ${expiryDate}...*\n` +
            `_Connecting to Delta Exchange and CoinDCX live books..._`, chatId);
        try {
            const results = await this.engine.scanOptions(assetKey, expiryDate);
            if (results.length === 0 || results[0].allStrikes.length === 0) {
                await this.sendMessage(`⚠️ No active options contracts found for *${asset.symbol}* expiring *${expiryDate}*.\n` +
                    `Try selecting another expiry or checking market hours.`, chatId);
                return;
            }
            const chain = results[0];
            const spotStr = chain.spotPrice.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            const spotInr = Math.round(chain.spotPrice * config_1.Config.USD_INR_RATE).toLocaleString("en-IN");
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
            }
            else {
                text += `ℹ️ _No strict CoinDCX ≥ Delta spread right now. Market ATM/OTM rates below:_\n\n`;
            }
            // ── ATM Strikes ──
            text += `📍 *ATM STRIKES (Around ${chain.atmStrike.toLocaleString()})*\n`;
            text += `─────────────────────\n`;
            for (const c of chain.atmCalls)
                text += this.formatStrikeRow(c);
            for (const p of chain.atmPuts)
                text += this.formatStrikeRow(p);
            text += `\n`;
            // ── OTM Calls ──
            if (chain.otmCalls.length > 0) {
                text += `📈 *OTM CALLS (Above Spot)*\n`;
                text += `─────────────────────\n`;
                for (const c of chain.otmCalls)
                    text += this.formatStrikeRow(c);
                text += `\n`;
            }
            // ── OTM Puts ──
            if (chain.otmPuts.length > 0) {
                text += `📉 *OTM PUTS (Below Spot)*\n`;
                text += `─────────────────────\n`;
                for (const p of chain.otmPuts)
                    text += this.formatStrikeRow(p);
                text += `\n`;
            }
            text += `👉 *Tap any button below to instantly execute the trade live:*`;
            // ── Clickable Execute Buttons ──
            const keyboard = [];
            for (const s of chain.allStrikes) {
                const tag = s.isSuggested ? "🟢 ARB" : "⚪";
                const label = `${tag} ${s.strike.toLocaleString()} ${s.optionType} | Sprd: ${s.spreadUsd >= 0 ? "+" : ""}$${s.spreadUsd}`;
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
        }
        catch (err) {
            await this.sendMessage(`❌ Scan error: ${err.message}`, chatId);
        }
    }
    /**
     * Strike row formatting.
     */
    formatStrikeRow(s, highlight = false) {
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
    async handleExecuteTrade(chatId, callbackData) {
        // Format: exec_BTC_96000_CALL_2026-11-04
        const parts = callbackData.replace("exec_", "").split("_");
        if (parts.length < 4) {
            await this.sendMessage("❌ Invalid execution parameters", chatId);
            return;
        }
        const assetKey = parts[0];
        const strikeNum = parseFloat(parts[1]);
        const optionType = parts[2];
        const expiryDate = parts.slice(3).join("-");
        const asset = config_1.ASSETS[assetKey];
        if (!asset) {
            await this.sendMessage(`❌ Unknown asset: ${assetKey}`, chatId);
            return;
        }
        await this.sendMessage(`⚡ *EXECUTING LIVE ARBITRAGE TRADE...*\n` +
            `━━━━━━━━━━━━━━━━━━━━━━\n` +
            `🪙 *${asset.symbol}* | Strike: *${strikeNum.toLocaleString()} ${optionType}*\n` +
            `📅 Expiry: *${expiryDate}*\n\n` +
            `🏛 *Delta Exchange:* BUY ${asset.deltaLots} lots (LONG)\n` +
            `🏦 *CoinDCX:* SELL ${asset.coindcxLots} lot(s) (SHORT)\n` +
            `📦 Total Size: *${asset.lotSizeUnits} ${asset.symbol}*\n\n` +
            `_Dispatching live signed API orders to exchanges..._`, chatId);
        try {
            const results = await this.engine.scanOptions(assetKey, expiryDate);
            const chain = results[0];
            if (!chain) {
                await this.sendMessage(`❌ Could not refresh options pricing for ${expiryDate}`, chatId);
                return;
            }
            const strikeData = chain.allStrikes.find((s) => s.strike === strikeNum && s.optionType === optionType);
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
            const keyboard = [
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
        }
        catch (err) {
            await this.sendMessage(`❌ Execution failed: ${err.message}`, chatId);
        }
    }
    /**
     * Live Net PnL & Open Positions Screen.
     */
    async handleShowNetPnl(chatId) {
        await this.sendMessage("📊 *Fetching Live Net PnL & Positions from Delta & CoinDCX...*", chatId);
        try {
            const pnlReport = await this.engine.getLiveNetPnl();
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
            }
            else {
                for (const p of pnlReport.deltaPositions) {
                    const sign = p.unrealizedPnlUsd >= 0 ? "+" : "";
                    lines.push(`   • *${p.symbol}*: ${p.side} ${p.size} lots\n` +
                        `     Entry: $${p.entryPrice.toFixed(2)} | Mark: $${p.markPrice.toFixed(2)}\n` +
                        `     PnL: *${sign}$${p.unrealizedPnlUsd.toFixed(2)}* (₹${p.unrealizedPnlInr.toLocaleString("en-IN")})`);
                }
                lines.push(`   👉 *Delta Subtotal:* ${pnlReport.deltaUnrealizedUsd >= 0 ? "+" : ""}$${pnlReport.deltaUnrealizedUsd.toFixed(2)}`);
            }
            lines.push(`\n🏦 *CoinDCX Positions:*`);
            if (pnlReport.coindcxPositions.length === 0) {
                lines.push("   • _No open positions (Flat)_");
            }
            else {
                for (const p of pnlReport.coindcxPositions) {
                    const sign = p.unrealizedPnlUsd >= 0 ? "+" : "";
                    lines.push(`   • *${p.symbol}*: ${p.side} ${p.size} lots\n` +
                        `     Entry: $${p.entryPrice.toFixed(2)} | Mark: $${p.markPrice.toFixed(2)}\n` +
                        `     PnL: *${sign}$${p.unrealizedPnlUsd.toFixed(2)}* (₹${p.unrealizedPnlInr.toLocaleString("en-IN")})`);
                }
                lines.push(`   👉 *CoinDCX Subtotal:* ${pnlReport.coindcxUnrealizedUsd >= 0 ? "+" : ""}$${pnlReport.coindcxUnrealizedUsd.toFixed(2)}`);
            }
            lines.push(`\n📊 *Status:* ${pnlReport.isFlat ? "🟢 FLAT (Ready for entry)" : "🟡 ACTIVE ARBITRAGE IN PROGRESS"}`);
            lines.push(`⏰ *Auto-Close:* All positions automatically close at 1:30 PM IST.`);
            const keyboard = [
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
        }
        catch (err) {
            await this.sendMessage(`❌ Error fetching Net PnL: ${err.message}`, chatId);
        }
    }
    /**
     * Live Wallet Balances Screen.
     */
    async handleShowWallet(chatId) {
        await this.sendMessage("💼 *Fetching Live Wallet Balances from Delta & CoinDCX...*", chatId);
        try {
            const report = await this.engine.getLiveWalletBalances();
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
            const cdcxWalletLabel = report.coindcx.walletName || "Options Wallet";
            lines.push(`\n🏦 *CoinDCX ${cdcxWalletLabel} (Live):*`);
            lines.push(`   • Total Balance: *$${report.coindcx.balanceUsd.toFixed(2)}* (₹${report.coindcx.balanceInr.toLocaleString("en-IN")})`);
            lines.push(`   • Available Options Margin: *$${report.coindcx.availableUsd.toFixed(2)}* (₹${report.coindcx.availableInr.toLocaleString("en-IN")})`);
            if (report.coindcx.lockedMarginInr !== undefined && report.coindcx.lockedMarginInr > 0) {
                lines.push(`   • Locked Options Margin: *₹${report.coindcx.lockedMarginInr.toLocaleString("en-IN")}* ($${report.coindcx.lockedMarginUsd?.toFixed(2)})`);
            }
            if (report.coindcx.currencies.length > 0) {
                for (const c of report.coindcx.currencies) {
                    lines.push(`   • ${c.symbol}: \`${c.total.toFixed(4)}\` (Avail: \`${c.available.toFixed(4)}\`)`);
                }
            }
            if (report.coindcx.error) {
                lines.push(`   • ⚠️ Status: ${report.coindcx.error}`);
            }
            lines.push(`\n📦 *TRADE LOT SIZING:*`);
            lines.push(`   • 1 Trade = 0.1 BTC / ETH / XAUT`);
            lines.push(`   • Delta: 10 lots | CoinDCX: 1 lot`);
            const canTrade = report.delta.availableUsd > 10 && report.coindcx.availableUsd > 10;
            if (canTrade) {
                lines.push(`   • Status: 🟢 *MARGIN READY ON BOTH EXCHANGES*`);
            }
            else {
                lines.push(`   • Status: ⚠️ *Ensure both accounts hold sufficient margin before trading*`);
            }
            const keyboard = [
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
            await this.sendMessage(lines.join("\n"), chatId, keyboard);
        }
        catch (err) {
            await this.sendMessage(`❌ Error checking wallet: ${err.message}`, chatId);
        }
    }
    /**
     * Close All Open Positions Handler.
     */
    async handleCloseAll(chatId) {
        await this.sendMessage("🚨 *CLOSING ALL POSITIONS LIVE ON DELTA & COINDCX...*\n" +
            "_Submitting market orders to flatten both exchange accounts..._", chatId);
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
            }
            else {
                lines.push("   • No open positions found on Delta.");
            }
            lines.push("");
            lines.push(`🏦 *CoinDCX:* Closed ${result.coindcx.closedCount} position(s)`);
            if (result.coindcx.details.length > 0) {
                lines.push(...result.coindcx.details.map((d) => `   • ${d}`));
            }
            else {
                lines.push("   • No open positions found on CoinDCX.");
            }
            lines.push("\n✅ Both legs executed! Send /pnl or tap below to verify flat state.");
            const keyboard = [
                [
                    { text: "📈 Verify Net PnL", callback_data: "view_pnl" },
                    { text: "🔙 Main Menu", callback_data: "main_menu" },
                ],
            ];
            await this.sendMessage(lines.join("\n"), chatId, keyboard);
        }
        catch (err) {
            await this.sendMessage(`❌ Error closing positions: ${err.message}`, chatId);
        }
    }
    /**
     * Help Message.
     */
    async showHelp(chatId) {
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
        await this.sendMessage(text, chatId);
    }
    /**
     * Process incoming Telegram text command or action.
     */
    async processCommand(text, userChatId) {
        const raw = text.trim().toLowerCase();
        if (raw === "/start" || raw === "/menu" || raw === "menu") {
            await this.sendMainMenu(userChatId);
        }
        else if (raw.includes("pnl") || raw.includes("profit") || raw.includes("position")) {
            await this.handleShowNetPnl(userChatId);
        }
        else if (raw.includes("wallet") || raw.includes("balance")) {
            await this.handleShowWallet(userChatId);
        }
        else if (raw === "/close" || raw === "close" || raw === "close all") {
            await this.handleCloseAll(userChatId);
        }
        else if (raw.includes("btc")) {
            await this.showExpirySelection(userChatId, "BTC");
        }
        else if (raw.includes("eth")) {
            await this.showExpirySelection(userChatId, "ETH");
        }
        else if (raw.includes("xaut") || raw.includes("gold")) {
            await this.showExpirySelection(userChatId, "XAUTUSDT");
        }
        else if (raw.includes("help")) {
            await this.showHelp(userChatId);
        }
        else {
            await this.sendMainMenu(userChatId);
        }
    }
    /**
     * Process incoming callback query from inline buttons.
     */
    async processCallbackQuery(callbackQuery) {
        const queryId = callbackQuery.id;
        const data = callbackQuery.data || "";
        const chatId = callbackQuery.message?.chat?.id?.toString() || this.defaultChatId;
        try {
            if (data.startsWith("asset_")) {
                const assetKey = data.replace("asset_", "");
                await this.answerCallbackQuery(queryId, `Selected ${assetKey}`);
                await this.showExpirySelection(chatId, assetKey);
            }
            else if (data.startsWith("expiry_")) {
                const parts = data.replace("expiry_", "").split("_");
                const assetKey = parts[0];
                const expiryDate = parts.slice(1).join("-");
                await this.answerCallbackQuery(queryId, `Scanning ${assetKey}...`);
                await this.handleScanOptions(chatId, assetKey, expiryDate);
            }
            else if (data.startsWith("exec_")) {
                await this.answerCallbackQuery(queryId, "⚡ Firing live order...");
                await this.handleExecuteTrade(chatId, data);
            }
            else if (data === "view_pnl") {
                await this.answerCallbackQuery(queryId, "📊 Fetching Net PnL...");
                await this.handleShowNetPnl(chatId);
            }
            else if (data === "view_wallet") {
                await this.answerCallbackQuery(queryId, "💼 Fetching Wallet...");
                await this.handleShowWallet(chatId);
            }
            else if (data === "close_all_confirm") {
                await this.answerCallbackQuery(queryId, "🛑 Closing all positions...");
                await this.handleCloseAll(chatId);
            }
            else if (data === "main_menu") {
                await this.answerCallbackQuery(queryId);
                await this.sendMainMenu(chatId);
            }
            else {
                await this.answerCallbackQuery(queryId);
            }
        }
        catch (err) {
            console.error(`Callback error: ${err.message}`);
            await this.answerCallbackQuery(queryId, `Error: ${err.message}`);
            await this.sendMessage(`❌ Error: ${err.message}`, chatId);
        }
    }
    /**
     * Start long-polling Telegram getUpdates loop.
     */
    async startPolling() {
        if (!this.apiUrl) {
            console.log("Telegram Bot Token not configured. Polling disabled.");
            return;
        }
        this.isRunning = true;
        console.log("🤖 Telegram Bot long-polling started...");
        console.log("📋 Commands: /start, /pnl, /wallet, /close, /help");
        console.log("⏰ Auto-close scheduled: 1:30 PM IST daily.");
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
                    timeout: 25000,
                });
                const updates = resp.data?.result || [];
                for (const update of updates) {
                    this.lastUpdateId = Math.max(this.lastUpdateId, update.update_id || 0);
                    if (update.callback_query) {
                        await this.processCallbackQuery(update.callback_query);
                    }
                    else if (update.message && update.message.text) {
                        const text = update.message.text;
                        const fromChat = update.message.chat?.id ? update.message.chat.id.toString() : this.defaultChatId;
                        console.log(`Telegram Command: '${text}' from chat ${fromChat}`);
                        await this.processCommand(text, fromChat);
                    }
                }
            }
            catch (err) {
                if (err.response?.status === 409) {
                    console.warn("Telegram conflict (another instance polling). Waiting 5s...");
                    await new Promise((r) => setTimeout(r, 5000));
                }
                else if (err.code !== "ECONNABORTED") {
                    await new Promise((r) => setTimeout(r, 3000));
                }
            }
        }
    }
    stop() {
        this.isRunning = false;
    }
}
exports.ArbTelegramBot = ArbTelegramBot;
