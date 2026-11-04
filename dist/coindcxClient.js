"use strict";
/**
 * CoinDCX API Client (LIVE ONLY).
 * Handles:
 *  - Live wallet balance (USDT, INR, etc.)
 *  - Live open positions & Unrealized PnL
 *  - Live order placement (SELL / SHORT)
 *  - Live position closing
 *  - Live options pricing & book summaries
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.CoinDCXClient = void 0;
const crypto_1 = __importDefault(require("crypto"));
const axios_1 = __importDefault(require("axios"));
const config_1 = require("./config");
class CoinDCXClient {
    apiKey;
    apiSecret;
    baseUrl;
    http;
    constructor() {
        this.apiKey = config_1.Config.COINDCX_API_KEY;
        this.apiSecret = config_1.Config.COINDCX_API_SECRET;
        this.baseUrl = config_1.Config.COINDCX_BASE_URL.replace(/\/+$/, "");
        this.http = axios_1.default.create({
            baseURL: this.baseUrl,
            timeout: 15000,
            headers: {
                "Content-Type": "application/json",
            },
        });
    }
    /**
     * Cryptographically sign CoinDCX request body with HMAC-SHA256.
     */
    sign(body) {
        const bodyStr = JSON.stringify(body);
        const signature = crypto_1.default
            .createHmac("sha256", this.apiSecret)
            .update(bodyStr)
            .digest("hex");
        return {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": this.apiKey,
            "X-AUTH-SIGNATURE": signature,
        };
    }
    /**
     * Fetch LIVE Options Wallet balances directly from CoinDCX.
     * CoinDCX Crypto Options are INR & USDT backed margin accounts.
     */
    async getWalletBalance() {
        if (!this.apiKey || !this.apiSecret) {
            return {
                exchange: "CoinDCX",
                walletName: "Options Wallet",
                balanceUsd: 0,
                balanceInr: 0,
                availableUsd: 0,
                availableInr: 0,
                currencies: [],
                error: "CoinDCX API credentials not set in .env",
            };
        }
        // 1. Try derivatives/options margin positions payload
        const positionsPath = "/exchange/v1/derivatives/futures/positions";
        const posBody = {
            timestamp: Math.floor(Date.now() / 1000),
            page: "1",
            size: "10",
            margin_currency_short_name: ["INR", "USDT"],
        };
        try {
            const headers = this.sign(posBody);
            const resp = await this.http.post(positionsPath, posBody, { headers });
            const rawData = resp.data;
            if (rawData && (rawData.total_wallet_balance !== undefined || rawData.available_balance_cross !== undefined)) {
                const totalBal = parseFloat(rawData.total_wallet_balance || "0");
                const availBal = parseFloat(rawData.available_balance_cross || rawData.available_balance_isolated || rawData.total_wallet_balance || "0");
                const lockedBal = parseFloat(rawData.total_initial_margin || "0");
                const isUsdt = (rawData.margin_currency || "").toUpperCase() === "USDT";
                const totalInr = isUsdt ? totalBal * config_1.Config.USD_INR_RATE : totalBal;
                const totalUsd = isUsdt ? totalBal : totalBal / config_1.Config.USD_INR_RATE;
                const availInr = isUsdt ? availBal * config_1.Config.USD_INR_RATE : availBal;
                const availUsd = isUsdt ? availBal : availBal / config_1.Config.USD_INR_RATE;
                const lockedInr = isUsdt ? lockedBal * config_1.Config.USD_INR_RATE : lockedBal;
                const lockedUsd = isUsdt ? lockedBal : lockedBal / config_1.Config.USD_INR_RATE;
                return {
                    exchange: "CoinDCX",
                    walletName: "Options Wallet",
                    balanceUsd: Math.round(totalUsd * 100) / 100,
                    balanceInr: Math.round(totalInr * 100) / 100,
                    availableUsd: Math.round(availUsd * 100) / 100,
                    availableInr: Math.round(availInr * 100) / 100,
                    lockedMarginInr: Math.round(lockedInr * 100) / 100,
                    lockedMarginUsd: Math.round(lockedUsd * 100) / 100,
                    currencies: [
                        { symbol: isUsdt ? "USDT" : "INR", total: totalBal, available: availBal },
                    ],
                };
            }
        }
        catch {
            // Fallback to balances
        }
        // 2. Query /exchange/v1/users/balances and isolate INR & USDT for options trading margin
        const path = "/exchange/v1/users/balances";
        const body = { timestamp: Math.floor(Date.now() / 1000) };
        try {
            const headers = this.sign(body);
            const resp = await this.http.post(path, body, { headers });
            if (resp.status === 200 && Array.isArray(resp.data)) {
                let totalUsd = 0;
                let availableUsd = 0;
                let totalInr = 0;
                let availableInr = 0;
                let lockedInr = 0;
                let lockedUsd = 0;
                const currencies = [];
                for (const item of resp.data) {
                    const curr = (item.currency || item.currency_short_name || "").toUpperCase();
                    const bal = parseFloat(item.balance || "0");
                    const locked = parseFloat(item.locked_balance || "0");
                    const avail = Math.max(0, bal - locked);
                    if (bal === 0 && avail === 0)
                        continue;
                    currencies.push({ symbol: curr, total: bal, available: avail });
                    if (curr === "INR") {
                        // Primary INR margin for CoinDCX Options
                        totalInr += bal;
                        availableInr += avail;
                        lockedInr += locked;
                        totalUsd += bal / config_1.Config.USD_INR_RATE;
                        availableUsd += avail / config_1.Config.USD_INR_RATE;
                        lockedUsd += locked / config_1.Config.USD_INR_RATE;
                    }
                    else if (["USDT", "USD"].includes(curr)) {
                        // USDT margin
                        totalUsd += bal;
                        availableUsd += avail;
                        lockedUsd += locked;
                        totalInr += bal * config_1.Config.USD_INR_RATE;
                        availableInr += avail * config_1.Config.USD_INR_RATE;
                        lockedInr += locked * config_1.Config.USD_INR_RATE;
                    }
                }
                return {
                    exchange: "CoinDCX",
                    walletName: "Options Wallet",
                    balanceUsd: Math.round(totalUsd * 100) / 100,
                    balanceInr: Math.round(totalInr * 100) / 100,
                    availableUsd: Math.round(availableUsd * 100) / 100,
                    availableInr: Math.round(availableInr * 100) / 100,
                    lockedMarginInr: Math.round(lockedInr * 100) / 100,
                    lockedMarginUsd: Math.round(lockedUsd * 100) / 100,
                    currencies,
                };
            }
            return {
                exchange: "CoinDCX",
                walletName: "Options Wallet",
                balanceUsd: 0,
                balanceInr: 0,
                availableUsd: 0,
                availableInr: 0,
                currencies: [],
                error: `CoinDCX Options wallet error: HTTP ${resp.status}`,
            };
        }
        catch (err) {
            return {
                exchange: "CoinDCX",
                walletName: "Options Wallet",
                balanceUsd: 0,
                balanceInr: 0,
                availableUsd: 0,
                availableInr: 0,
                currencies: [],
                error: err.response?.data?.message || err.message,
            };
        }
    }
    /**
     * Fetch LIVE open positions directly from CoinDCX Derivatives.
     */
    async getOpenPositions() {
        if (!this.apiKey || !this.apiSecret)
            return [];
        const path = "/exchange/v1/derivatives/futures/positions";
        const body = {
            timestamp: Math.floor(Date.now() / 1000),
            page: "1",
            size: "50",
            margin_currency_short_name: ["USDT", "INR"],
        };
        try {
            const headers = this.sign(body);
            const resp = await this.http.post(path, body, { headers });
            const rawPositions = resp.data?.data || resp.data || [];
            const positions = [];
            for (const p of rawPositions) {
                const qty = parseFloat(p.active_pos || p.quantity || p.size || "0");
                if (Math.abs(qty) === 0)
                    continue;
                const sym = p.pair || p.symbol || "";
                const entryPrice = parseFloat(p.avg_price || p.entry_price || "0");
                const markPrice = parseFloat(p.mark_price || p.current_price || "0");
                const unrealizedPnl = parseFloat(p.unrealized_pnl || p.pnl || "0");
                const side = (p.side || (qty < 0 ? "sell" : "buy")).toUpperCase() === "BUY" ? "LONG" : "SHORT";
                positions.push({
                    exchange: "CoinDCX",
                    symbol: sym,
                    side,
                    size: Math.abs(qty),
                    entryPrice,
                    markPrice,
                    unrealizedPnlUsd: Math.round(unrealizedPnl * 100) / 100,
                    unrealizedPnlInr: Math.round(unrealizedPnl * config_1.Config.USD_INR_RATE * 100) / 100,
                    leverage: parseFloat(p.leverage || "1"),
                    raw: p,
                });
            }
            return positions;
        }
        catch (err) {
            console.warn(`CoinDCX positions error: ${err.message}`);
            return [];
        }
    }
    /**
     * Fetch options chain data for the target asset and expiry dates.
     */
    async getOptions(asset, expiryDates) {
        // 1. Try CoinDCX native derivatives market data
        try {
            const resp = await this.http.get("/exchange/v1/derivatives/futures/data/instruments");
            const instruments = resp.data || [];
            const optionInstruments = instruments.filter((inst) => {
                const pair = (inst.pair || inst.symbol || "").toUpperCase();
                const contractType = (inst.contract_type || inst.type || "").toLowerCase();
                return (pair.includes(asset.coindcxPrefix.toUpperCase()) &&
                    (contractType.includes("option") || contractType.includes("call") || contractType.includes("put")));
            });
            const parsed = [];
            const nowUtc = new Date();
            const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);
            for (const inst of optionInstruments) {
                const expiry = inst.expiry || inst.settlement_time || inst.expiry_date;
                if (!expiry)
                    continue;
                const expiryDt = new Date(expiry);
                if (isNaN(expiryDt.getTime()) || expiryDt < oneHourAgo)
                    continue;
                const istDt = new Date(expiryDt.getTime() + 5.5 * 3600 * 1000);
                const istDateStr = istDt.toISOString().split("T")[0];
                if (!expiryDates.includes(istDateStr))
                    continue;
                const contractType = (inst.contract_type || inst.type || "").toLowerCase();
                const optionType = contractType.includes("call") ? "CALL" : "PUT";
                const strike = parseFloat(inst.strike_price || inst.strike || "0");
                if (isNaN(strike) || strike <= 0)
                    continue;
                const markPrice = parseFloat(inst.mark_price || inst.last_price || "0");
                const bestBid = parseFloat(inst.best_bid || inst.bid || "0");
                const bestAsk = parseFloat(inst.best_ask || inst.ask || "0");
                const iv = parseFloat(inst.iv || inst.implied_volatility || "0");
                parsed.push({
                    exchange: "CoinDCX",
                    symbol: inst.pair || inst.symbol,
                    instrumentName: inst.pair || inst.symbol,
                    underlying: asset.symbol,
                    strike,
                    optionType,
                    markPrice,
                    bestBid,
                    bestAsk,
                    ivPct: Math.round(iv * 100) / 100,
                    expiryDate: istDateStr,
                    expiryLabel: `${istDateStr} 1:30 PM IST`,
                    settleDtUtc: expiryDt,
                    raw: inst,
                });
            }
            if (parsed.length > 0)
                return parsed;
        }
        catch {
            // Continue to live Deribit liquidity fallback
        }
        // 2. Fetch live options data from Deribit (source of CoinDCX crypto options liquidity)
        return this.getDeribitOptions(asset, expiryDates);
    }
    /**
     * Fetch live options book from Deribit (institutional backend for crypto options).
     */
    async getDeribitOptions(asset, expiryDates) {
        const currency = asset.deltaUnderlying === "XAUT" ? "BTC" : asset.deltaUnderlying;
        const url = `https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=${currency}&kind=option`;
        try {
            const resp = await axios_1.default.get(url, { timeout: 15000 });
            const data = resp.data?.result || [];
            const parsed = [];
            const months = {
                JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
                JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
            };
            let spotPrice = 0;
            try {
                const spotResp = await axios_1.default.get(`https://www.deribit.com/api/v2/public/get_index_price?index_name=${currency.toLowerCase()}_usd`, { timeout: 8000 });
                spotPrice = spotResp.data?.result?.index_price || 0;
            }
            catch {
                spotPrice = 0;
            }
            const nowUtc = new Date();
            const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);
            for (const item of data) {
                const instrument = item.instrument_name || "";
                const parts = instrument.split("-");
                if (parts.length < 4)
                    continue;
                const [assetStr, expiryStr, strikeStr, optChar] = parts;
                if (assetStr !== currency)
                    continue;
                const optionType = optChar.toUpperCase() === "C" ? "CALL" : "PUT";
                const strike = parseFloat(strikeStr);
                if (isNaN(strike))
                    continue;
                const dayMatch = expiryStr.match(/^(\d{1,2})([A-Z]{3})(\d{2})$/);
                if (!dayMatch)
                    continue;
                const day = parseInt(dayMatch[1], 10);
                const mon = months[dayMatch[2]];
                const year = 2000 + parseInt(dayMatch[3], 10);
                if (mon === undefined)
                    continue;
                const settleDtUtc = new Date(Date.UTC(year, mon, day, 8, 0, 0)); // 08:00 UTC = 1:30 PM IST
                if (settleDtUtc < oneHourAgo)
                    continue;
                const istDt = new Date(settleDtUtc.getTime() + 5.5 * 3600 * 1000);
                const istDateStr = istDt.toISOString().split("T")[0];
                if (!expiryDates.includes(istDateStr))
                    continue;
                const btcMark = parseFloat(item.mark_price || "0");
                const btcBid = parseFloat(item.best_bid_price || "0");
                const btcAsk = parseFloat(item.best_ask_price || "0");
                const markIv = parseFloat(item.mark_iv || "0");
                const markUsd = spotPrice > 0 ? btcMark * spotPrice : 0;
                const bidUsd = spotPrice > 0 ? btcBid * spotPrice : 0;
                const askUsd = spotPrice > 0 ? btcAsk * spotPrice : 0;
                parsed.push({
                    exchange: "CoinDCX",
                    symbol: instrument,
                    instrumentName: instrument,
                    underlying: asset.symbol,
                    strike,
                    optionType,
                    markPrice: Math.round(markUsd * 100) / 100,
                    bestBid: Math.round(bidUsd * 100) / 100,
                    bestAsk: Math.round(askUsd * 100) / 100,
                    ivPct: Math.round(markIv * 100) / 100,
                    expiryDate: istDateStr,
                    expiryLabel: `${istDateStr} 1:30 PM IST`,
                    settleDtUtc,
                    raw: item,
                });
            }
            return parsed;
        }
        catch (err) {
            console.error(`Options data fetch error: ${err.message}`);
            return [];
        }
    }
    /**
     * Place a direct LIVE SELL order (SHORT) on CoinDCX.
     */
    async placeSellOrder(instrumentSymbol, quantity, price) {
        if (!this.apiKey || !this.apiSecret) {
            return { success: false, message: "CoinDCX credentials missing in .env" };
        }
        const body = {
            timestamp: Math.floor(Date.now() / 1000),
            side: "sell",
            order_type: price ? "limit_order" : "market_order",
            pair: instrumentSymbol,
            total_quantity: quantity,
        };
        if (price && price > 0) {
            body.price = price;
        }
        try {
            const headers = this.sign(body);
            const resp = await this.http.post("/exchange/v1/derivatives/futures/orders/create", body, { headers });
            if (resp.status === 200 || resp.status === 201) {
                const orderId = resp.data?.id || resp.data?.order_id || "N/A";
                return {
                    success: true,
                    message: `CoinDCX SELL filled! Order ID: #${orderId} (${quantity} lot(s) of ${instrumentSymbol})`,
                    data: resp.data,
                };
            }
            return { success: false, message: `CoinDCX order rejected: ${JSON.stringify(resp.data)}` };
        }
        catch (err) {
            const msg = err.response?.data?.message || err.response?.data?.error || err.message;
            return { success: false, message: `CoinDCX order failed: ${msg}` };
        }
    }
    /**
     * Close a single live position on CoinDCX by sending an opposite order.
     */
    async closePosition(symbol, quantity, currentSide) {
        if (!this.apiKey || !this.apiSecret) {
            return { success: false, message: "CoinDCX credentials missing" };
        }
        const side = currentSide.toLowerCase().includes("sell") || currentSide.toLowerCase().includes("short")
            ? "buy"
            : "sell";
        const body = {
            timestamp: Math.floor(Date.now() / 1000),
            side,
            order_type: "market_order",
            pair: symbol,
            total_quantity: Math.abs(quantity),
        };
        try {
            const headers = this.sign(body);
            const resp = await this.http.post("/exchange/v1/derivatives/futures/orders/create", body, { headers });
            if (resp.status === 200 || resp.status === 201) {
                return { success: true, message: `Closed ${Math.abs(quantity)} lot(s) of ${symbol} (${side})` };
            }
            return { success: false, message: `Close failed: ${JSON.stringify(resp.data)}` };
        }
        catch (err) {
            return { success: false, message: err.response?.data?.message || err.message };
        }
    }
    /**
     * Close ALL open positions on CoinDCX.
     */
    async closeAllPositions() {
        const positions = await this.getOpenPositions();
        const details = [];
        let closedCount = 0;
        for (const p of positions) {
            const sym = p.symbol;
            const qty = p.size;
            const side = p.side;
            if (Math.abs(qty) > 0) {
                const res = await this.closePosition(sym, qty, side);
                details.push(`${sym}: ${res.message}`);
                if (res.success)
                    closedCount++;
            }
        }
        return { closedCount, details };
    }
}
exports.CoinDCXClient = CoinDCXClient;
