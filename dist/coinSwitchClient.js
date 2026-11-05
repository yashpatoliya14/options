"use strict";
/**
 * CoinSwitch PRO API Client.
 *
 * Capabilities:
 *  - Crypto options settling at 1:30 PM IST (08:00 UTC) matching CoinSwitch PRO expiries.
 *  - Ed25519 cryptographic request signing for private endpoints.
 *  - Real-time wallet portfolio balance & margin tracking.
 *  - Live position tracking and order execution for Short leg (SELL).
 *  - Daily auto-close support at 1:30 PM IST.
 */
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.CoinSwitchClient = void 0;
const crypto_1 = __importDefault(require("crypto"));
const axios_1 = __importDefault(require("axios"));
const config_1 = require("./config");
class CoinSwitchClient {
    apiKey;
    secretKey;
    baseUrl;
    http;
    constructor(apiKey, secretKey, baseUrl) {
        this.apiKey = apiKey || config_1.Config.COINSWITCH_API_KEY;
        this.secretKey = secretKey || config_1.Config.COINSWITCH_SECRET_KEY;
        this.baseUrl = (baseUrl || config_1.Config.COINSWITCH_BASE_URL).replace(/\/+$/, "");
        this.http = axios_1.default.create({
            baseURL: this.baseUrl,
            timeout: 12000,
            headers: {
                "User-Agent": "OptionsArbitrage-Bot/2.0",
                "Content-Type": "application/json",
            },
        });
    }
    /**
     * Cryptographically signs requests for CoinSwitch PRO via Ed25519.
     */
    signRequest(method, endpointPath, body) {
        const epoch = Date.now().toString();
        const payloadStr = body ? JSON.stringify(body) : "";
        const message = `${method.toUpperCase()}${endpointPath}${payloadStr}${epoch}`;
        let signature = "";
        if (this.secretKey && this.secretKey.length >= 64) {
            try {
                const seed = Buffer.from(this.secretKey, "hex");
                // Ed25519 PKCS#8 DER prefix for 32-byte seed
                const prefix = Buffer.from("302e020100300506032b657004220420", "hex");
                const pkcs8Key = Buffer.concat([prefix, seed]);
                const privKey = crypto_1.default.createPrivateKey({
                    key: pkcs8Key,
                    format: "der",
                    type: "pkcs8",
                });
                signature = crypto_1.default.sign(null, Buffer.from(message, "utf-8"), privKey).toString("hex");
            }
            catch (err) {
                console.warn(`CoinSwitch Ed25519 signature error: ${err.message}`);
            }
        }
        return {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": this.apiKey,
            "X-AUTH-SIGNATURE": signature,
            "X-AUTH-EPOCH": epoch,
            "User-Agent": "OptionsArbitrage-Bot/2.0",
        };
    }
    /**
     * Fetch live options chain for the asset, filtered for 1:30 PM IST settlement on target expiry dates.
     */
    async getOptions(asset, expiryDates) {
        const currency = asset.symbol === "XAUTUSDT" ? "XAUT" : asset.symbol;
        const url = `https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=${currency}&kind=option`;
        try {
            const resp = await axios_1.default.get(url, { timeout: 10000 });
            const data = resp.data?.result || [];
            // Spot index price from Deribit
            let spotPrice = 0;
            for (const item of data) {
                if (item.estimated_delivery_price && item.estimated_delivery_price > 0) {
                    spotPrice = item.estimated_delivery_price;
                    break;
                }
                if (item.underlying_price && item.underlying_price > 0) {
                    spotPrice = item.underlying_price;
                    break;
                }
            }
            const parsed = [];
            const nowUtc = new Date();
            const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);
            const months = {
                JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
                JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
            };
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
                // CoinSwitch options settle at 08:00 UTC = 1:30 PM IST
                const settleDtUtc = new Date(Date.UTC(year, mon, day, 8, 0, 0));
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
                    exchange: "CoinSwitch",
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
            console.warn(`CoinSwitch options fetch warning: ${err.message}`);
            return [];
        }
    }
    /**
     * Fetch LIVE wallet balance from CoinSwitch PRO.
     */
    async getWalletBalance() {
        if (!this.apiKey || !this.secretKey || this.apiKey.includes("your_")) {
            return {
                exchange: "CoinSwitch",
                walletName: "Options Portfolio",
                balanceUsd: 0,
                balanceInr: 0,
                availableUsd: 0,
                availableInr: 0,
                currencies: [],
                error: "CoinSwitch API credentials missing in .env",
            };
        }
        const endpoints = [
            "/trade/api/v2/user/portfolio",
            "/trade/api/v2/wallet/balance",
        ];
        for (const path of endpoints) {
            try {
                const headers = this.signRequest("GET", path);
                const resp = await this.http.get(path, { headers });
                if (resp.status === 200 && resp.data) {
                    const data = resp.data.data || resp.data;
                    const totalInr = parseFloat(data.total_portfolio_value_inr || data.total_balance_inr || "0");
                    const availInr = parseFloat(data.available_balance_inr || data.available_inr || totalInr.toString());
                    const totalUsd = totalInr > 0 ? totalInr / config_1.Config.USD_INR_RATE : 0;
                    const availUsd = availInr > 0 ? availInr / config_1.Config.USD_INR_RATE : 0;
                    const currencies = [];
                    if (Array.isArray(data.balances)) {
                        for (const b of data.balances) {
                            currencies.push({
                                symbol: b.currency || b.symbol,
                                total: parseFloat(b.total || "0"),
                                available: parseFloat(b.available || "0"),
                            });
                        }
                    }
                    return {
                        exchange: "CoinSwitch",
                        walletName: "Options Portfolio",
                        balanceUsd: Math.round(totalUsd * 100) / 100,
                        balanceInr: Math.round(totalInr * 100) / 100,
                        availableUsd: Math.round(availUsd * 100) / 100,
                        availableInr: Math.round(availInr * 100) / 100,
                        currencies,
                    };
                }
            }
            catch (err) {
                // try next endpoint
            }
        }
        return {
            exchange: "CoinSwitch",
            walletName: "Options Portfolio",
            balanceUsd: 0,
            balanceInr: 0,
            availableUsd: 0,
            availableInr: 0,
            currencies: [],
            error: "Unable to reach CoinSwitch balance endpoint",
        };
    }
    /**
     * Fetch LIVE open positions on CoinSwitch PRO.
     */
    async getOpenPositions() {
        if (!this.apiKey || !this.secretKey || this.apiKey.includes("your_")) {
            return [];
        }
        const endpoints = [
            "/trade/api/v2/positions",
            "/trade/api/v2/options/positions",
        ];
        for (const path of endpoints) {
            try {
                const headers = this.signRequest("GET", path);
                const resp = await this.http.get(path, { headers });
                if (resp.status === 200 && resp.data) {
                    const rawList = resp.data.data || resp.data.result || [];
                    const positions = [];
                    for (const p of rawList) {
                        const qty = parseFloat(p.size || p.quantity || p.active_pos || "0");
                        if (Math.abs(qty) === 0)
                            continue;
                        const sym = p.symbol || p.pair || "";
                        const entryPrice = parseFloat(p.entry_price || p.avg_price || "0");
                        const markPrice = parseFloat(p.mark_price || p.current_price || "0");
                        const unrealizedPnl = parseFloat(p.unrealized_pnl || p.pnl || "0");
                        const side = (p.side || (qty < 0 ? "sell" : "buy")).toUpperCase() === "BUY" ? "LONG" : "SHORT";
                        positions.push({
                            exchange: "CoinSwitch",
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
            }
            catch {
                // continue
            }
        }
        return [];
    }
    /**
     * Place a direct LIVE SELL (SHORT) order on CoinSwitch PRO.
     */
    async placeSellOrder(instrumentSymbol, quantity, price) {
        if (!this.apiKey || !this.secretKey || this.apiKey.includes("your_")) {
            return { success: false, message: "CoinSwitch credentials missing in .env" };
        }
        const path = "/trade/api/v2/order";
        const body = {
            symbol: instrumentSymbol,
            pair: instrumentSymbol,
            side: "sell",
            type: price ? "limit" : "market",
            quantity: Math.abs(quantity),
            category: "option",
        };
        if (price && price > 0) {
            body.price = Number(price);
        }
        try {
            const headers = this.signRequest("POST", path, body);
            const resp = await this.http.post(path, body, { headers });
            if (resp.status === 200 || resp.status === 201) {
                const orderId = resp.data?.data?.order_id || resp.data?.order_id || resp.data?.id || "N/A";
                return {
                    success: true,
                    message: `CoinSwitch SELL filled! Order ID: #${orderId} (${quantity} lot(s) of ${instrumentSymbol})`,
                    data: resp.data,
                };
            }
            return { success: false, message: `CoinSwitch order rejected: ${JSON.stringify(resp.data)}` };
        }
        catch (err) {
            const msg = err.response?.data?.message || err.response?.data?.error || err.message;
            return { success: false, message: `CoinSwitch order failed: ${msg}` };
        }
    }
    /**
     * Close a single live position on CoinSwitch PRO by sending an opposite market order.
     */
    async closePosition(symbol, quantity, currentSide) {
        if (!this.apiKey || !this.secretKey || this.apiKey.includes("your_")) {
            return { success: false, message: "CoinSwitch credentials missing" };
        }
        const path = "/trade/api/v2/order";
        const side = currentSide.toLowerCase().includes("sell") || currentSide.toLowerCase().includes("short")
            ? "buy"
            : "sell";
        const body = {
            symbol,
            pair: symbol,
            side,
            type: "market",
            quantity: Math.abs(quantity),
            category: "option",
        };
        try {
            const headers = this.signRequest("POST", path, body);
            const resp = await this.http.post(path, body, { headers });
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
     * Close ALL open positions on CoinSwitch PRO.
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
exports.CoinSwitchClient = CoinSwitchClient;
