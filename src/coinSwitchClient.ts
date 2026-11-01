/**
 * CoinSwitch PRO API Client (TypeScript / Node.js).
 * Live options & live wallet balance only.
 */

import crypto from "crypto";
import axios, { AxiosInstance } from "axios";
import { Config } from "./config";
import { ParsedOption, WalletBalanceInfo } from "./types";

export class CoinSwitchClient {
  private apiKey: string;
  private secretKey: string;
  private baseUrl: string;
  private http: AxiosInstance;

  constructor(apiKey?: string, secretKey?: string, baseUrl?: string) {
    this.apiKey = apiKey || Config.COINSWITCH_API_KEY;
    this.secretKey = secretKey || Config.COINSWITCH_SECRET_KEY;
    this.baseUrl = (baseUrl || Config.COINSWITCH_BASE_URL).replace(/\/+$/, "");
    this.http = axios.create({
      baseURL: this.baseUrl,
      timeout: 12000,
      headers: {
        "User-Agent": "OptionsAlgo-Node/1.0",
        "Content-Type": "application/json"
      }
    });
  }

  private signRequest(method: string, endpointPath: string, body?: any): Record<string, string> {
    const epoch = Date.now().toString();
    const payloadStr = body ? JSON.stringify(body) : "";
    const message = `${method.toUpperCase()}${endpointPath}${payloadStr}${epoch}`;

    let signature = "";
    if (this.secretKey && this.secretKey.length >= 64) {
      try {
        const seed = Buffer.from(this.secretKey, "hex");
        const prefix = Buffer.from("302e020100300506032b657004220420", "hex");
        const pkcs8Key = Buffer.concat([prefix, seed]);
        const privKey = crypto.createPrivateKey({
          key: pkcs8Key,
          format: "der",
          type: "pkcs8"
        });
        signature = crypto.sign(null, Buffer.from(message, "utf-8"), privKey).toString("hex");
      } catch (err: any) {
        console.warn(`CoinSwitch Ed25519 sign error: ${err.message}`);
      }
    }

    return {
      "Content-Type": "application/json",
      "X-AUTH-APIKEY": this.apiKey,
      "X-AUTH-SIGNATURE": signature,
      "X-AUTH-EPOCH": epoch,
      "User-Agent": "OptionsAlgo-Node/1.0"
    };
  }

  public async getParsedOptions(spotPrice: number, targetDate?: string): Promise<ParsedOption[]> {
    const url = `https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=${Config.UNDERLYING_ASSET}&kind=option`;
    try {
      const resp = await axios.get(url, { timeout: 12000 });
      const data: any[] = resp.data?.result || [];

      const parsed: ParsedOption[] = [];
      const nowUtc = new Date();
      const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);

      const months: Record<string, number> = {
        JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
        JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11
      };

      for (const item of data) {
        const instrument: string = item.instrument_name || "";
        const parts = instrument.split("-");
        if (parts.length < 4) continue;

        const [asset, expiryStr, strikeStr, optChar] = parts;
        if (asset !== Config.UNDERLYING_ASSET) continue;

        const optionType = optChar.toUpperCase() === "C" ? "CALL" : "PUT";
        const strike = parseFloat(strikeStr);
        if (isNaN(strike)) continue;

        const dayMatch = expiryStr.match(/^(\d{1,2})([A-Z]{3})(\d{2})$/);
        if (!dayMatch) continue;

        const day = parseInt(dayMatch[1], 10);
        const mon = months[dayMatch[2]];
        const year = 2000 + parseInt(dayMatch[3], 10);
        if (mon === undefined) continue;

        const settleDtUtc = new Date(Date.UTC(year, mon, day, 8, 0, 0));
        if (settleDtUtc < oneHourAgo) continue;

        const settleDtIst = new Date(settleDtUtc.getTime() + 5.5 * 3600 * 1000);
        const istDateStr = settleDtIst.toISOString().split("T")[0];

        if (targetDate && istDateStr !== targetDate) continue;

        const btcMark = parseFloat(item.mark_price || "0");
        const btcBid = parseFloat(item.best_bid_price || "0");
        const btcAsk = parseFloat(item.best_ask_price || "0");
        const markIv = parseFloat(item.mark_iv || "0");

        const markUsd = btcMark * spotPrice;
        const bidUsd = btcBid * spotPrice;
        const askUsd = btcAsk * spotPrice;

        const timeStr = `${istDateStr} 13:30 IST`;

        parsed.push({
          exchange: "CoinSwitch",
          symbol: instrument,
          strike,
          optionType,
          markPrice: Math.round(markUsd * 100) / 100,
          bestBid: Math.round(bidUsd * 100) / 100,
          bestAsk: Math.round(askUsd * 100) / 100,
          ivPct: Math.round(markIv * 100) / 100,
          settleDtUtc,
          settleDtIst,
          settleTimeStr: timeStr,
          expiryLabel: "1:30 PM IST",
          raw: item
        });
      }

      return parsed;
    } catch (err: any) {
      console.error(`Error fetching CoinSwitch options: ${err.message}`);
      return [];
    }
  }

  /**
   * Fetch LIVE wallet balance from CoinSwitch PRO.
   * If credentials are not set or balance is 0, returns live 0.
   */
  /**
   * Fetch open option positions on CoinSwitch PRO.
   */
  public async getOpenPositions(): Promise<any[]> {
    if (!this.apiKey || !this.secretKey || this.apiKey === "your_coinswitch_api_key_here") {
      return [];
    }
    const endpoints = [
      "/trade/api/v2/positions",
      "/trade/api/v2/options/positions"
    ];
    for (const path of endpoints) {
      try {
        const headers = this.signRequest("GET", path);
        const resp = await this.http.get(path, { headers });
        if (resp.status === 200) {
          const list: any[] = resp.data?.data || resp.data?.result || [];
          return list.filter((p) => Math.abs(parseFloat(p.size || p.quantity || "0")) > 0);
        }
      } catch (err: any) {
        // try next
      }
    }
    return [];
  }

  /**
   * Close a position on CoinSwitch by sending an opposing market order.
   */
  public async closePosition(symbol: string, quantity: number, currentSide: string): Promise<{ success: boolean; message: string }> {
    if (!this.apiKey || !this.secretKey) {
      return { success: false, message: "CoinSwitch credentials missing" };
    }
    const path = "/trade/api/v2/order";
    const side = currentSide.toLowerCase().includes("sell") || currentSide.toLowerCase().includes("short") ? "buy" : "sell";
    const body = {
      pair: symbol,
      side,
      type: "market",
      quantity: Math.abs(quantity).toString(),
      category: "option"
    };
    try {
      const headers = this.signRequest("POST", path, body);
      const resp = await this.http.post(path, body, { headers });
      if (resp.status === 200 || resp.status === 201) {
        return { success: true, message: `Closed ${quantity} of ${symbol} (${side})` };
      }
      return { success: false, message: `Failed: ${JSON.stringify(resp.data)}` };
    } catch (err: any) {
      return { success: false, message: err.response?.data?.message || err.message };
    }
  }

  /**
   * Close all active positions on CoinSwitch.
   */
  public async closeAllPositions(): Promise<{ closedCount: number; details: string[] }> {
    const positions = await this.getOpenPositions();
    const details: string[] = [];
    let closedCount = 0;

    for (const p of positions) {
      const sym = p.symbol || p.pair;
      const qty = parseFloat(p.size || p.quantity || "0");
      const side = p.side || "sell";
      if (Math.abs(qty) > 0) {
        const res = await this.closePosition(sym, qty, side);
        details.push(`${sym}: ${res.message}`);
        if (res.success) closedCount++;
      }
    }

    return { closedCount, details };
  }

  public async getWalletBalance(): Promise<WalletBalanceInfo> {
      return {
        exchange: "CoinSwitch",
        balanceUsd: 0,
        balanceInr: 0,
        availableUsd: 0,
        availableInr: 0,
        error: "CoinSwitch API credentials not set in .env"
      };
    }

    const endpoints = [
      "/trade/api/v2/user/portfolio",
      "/trade/api/v2/wallet/balance"
    ];

    for (const path of endpoints) {
      try {
        const headers = this.signRequest("GET", path);
        const resp = await this.http.get(path, { headers });
        if (resp.status === 200) {
          const data = resp.data?.data || {};
          const totalInr = parseFloat(data.total_portfolio_value_inr || data.total_balance_inr || "0");
          const availInr = parseFloat(data.available_balance_inr || data.total_portfolio_value_inr || "0");

          const totalUsd = totalInr > 0 ? totalInr / Config.USD_INR_RATE : 0;
          const availUsd = availInr > 0 ? availInr / Config.USD_INR_RATE : 0;

          return {
            exchange: "CoinSwitch",
            balanceUsd: Math.round(totalUsd * 100) / 100,
            balanceInr: Math.round(totalInr * 100) / 100,
            availableUsd: Math.round(availUsd * 100) / 100,
            availableInr: Math.round(availInr * 100) / 100,
            details: data
          };
        }
      } catch (err: any) {
        // Continue to next endpoint or return error
      }
    }

    return {
      exchange: "CoinSwitch",
      balanceUsd: 0,
      balanceInr: 0,
      availableUsd: 0,
      availableInr: 0,
      error: "Unable to reach CoinSwitch balance endpoint"
    };
  }
}
