/**
 * Delta Exchange API Client (TypeScript / Node.js).
 * Live options & live wallet balance only.
 */

import crypto from "crypto";
import axios, { AxiosInstance } from "axios";
import { Config } from "./config";
import { ParsedOption, WalletBalanceInfo } from "./types";

export class DeltaClient {
  private apiKey: string;
  private apiSecret: string;
  private baseUrl: string;
  private http: AxiosInstance;

  constructor(apiKey?: string, apiSecret?: string, baseUrl?: string) {
    this.apiKey = apiKey || Config.DELTA_API_KEY;
    this.apiSecret = apiSecret || Config.DELTA_API_SECRET;
    this.baseUrl = (baseUrl || Config.getDeltaBaseUrl()).replace(/\/+$/, "");
    this.http = axios.create({
      baseURL: this.baseUrl,
      timeout: 12000,
      headers: {
        "User-Agent": "OptionsAlgo-Node/1.0",
        Accept: "application/json"
      }
    });
  }

  private signRequest(method: string, path: string, queryStr = "", payloadStr = ""): Record<string, string> {
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const message = method.toUpperCase() + timestamp + path + queryStr + payloadStr;
    const signature = crypto.createHmac("sha256", this.apiSecret).update(message).digest("hex");

    return {
      "api-key": this.apiKey,
      timestamp,
      signature
    };
  }

  public async getSpotPrice(symbol?: string): Promise<number> {
    const sym = symbol || Config.UNDERLYING_SYMBOL;
    try {
      const resp = await this.http.get(`/v2/tickers/${sym}`);
      const data = resp.data?.result || {};
      const mark = parseFloat(data.mark_price || data.spot_price || "0");
      return mark;
    } catch (err: any) {
      console.error(`Error fetching Delta spot price: ${err.message}`);
      throw err;
    }
  }

  public async getOptionsTickers(): Promise<any[]> {
    try {
      const resp = await this.http.get("/v2/tickers?contract_types=call_options,put_options");
      const tickers: any[] = resp.data?.result || [];
      return tickers.filter((t) => t.underlying_asset_symbol === Config.UNDERLYING_ASSET);
    } catch (err: any) {
      console.error(`Error fetching Delta option tickers: ${err.message}`);
      return [];
    }
  }

  public async getOptionsProducts(): Promise<Record<string, any>> {
    try {
      const resp = await this.http.get("/v2/products");
      const products: any[] = resp.data?.result || [];
      const map: Record<string, any> = {};
      for (const p of products) {
        if (p.symbol) {
          map[p.symbol] = p;
        }
      }
      return map;
    } catch (err: any) {
      console.error(`Error fetching Delta products: ${err.message}`);
      return {};
    }
  }

  public async getParsedOptions(targetDate?: string): Promise<ParsedOption[]> {
    const [tickers, products] = await Promise.all([
      this.getOptionsTickers(),
      this.getOptionsProducts()
    ]);

    const parsed: ParsedOption[] = [];
    const nowUtc = new Date();
    const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);

    for (const t of tickers) {
      const sym = t.symbol || "";
      const prod = products[sym] || {};
      const settleStr = prod.settlement_time || t.settlement_time;
      if (!settleStr) continue;

      const settleDtUtc = new Date(settleStr);
      if (isNaN(settleDtUtc.getTime()) || settleDtUtc < oneHourAgo) continue;

      const settleDtIst = new Date(settleDtUtc.getTime() + 5.5 * 3600 * 1000);
      const istDateStr = settleDtIst.toISOString().split("T")[0];

      if (targetDate && istDateStr !== targetDate) continue;

      const quotes = t.quotes || {};
      const contractType = (t.contract_type || "").toLowerCase();
      const optionType = contractType.includes("call") ? "CALL" : "PUT";

      const strike = parseFloat(t.strike_price || "0");
      const markPrice = parseFloat(t.mark_price || "0");
      const bestBid = parseFloat(quotes.best_bid || "0");
      const bestAsk = parseFloat(quotes.best_ask || "0");
      const markIv = parseFloat(quotes.mark_iv || t.mark_vol || "0") * 100;

      if (isNaN(strike) || strike <= 0) continue;

      const timeStr = `${istDateStr} ${settleDtIst.toISOString().substring(11, 16)} IST`;

      parsed.push({
        exchange: "Delta Exchange",
        symbol: sym,
        strike,
        optionType,
        markPrice,
        bestBid,
        bestAsk,
        ivPct: Math.round(markIv * 100) / 100,
        settleDtUtc,
        settleDtIst,
        settleTimeStr: timeStr,
        expiryLabel: "5:30 PM IST",
        raw: t
      });
    }

    return parsed;
  }

  /**
   * Fetch LIVE wallet balance from Delta Exchange.
   * If credentials are not set or balance is 0, returns live 0.
   */
  /**
   * Fetch open option positions on Delta Exchange.
   */
  public async getOpenPositions(): Promise<any[]> {
    if (!this.apiKey || !this.apiSecret || this.apiKey === "your_delta_api_key_here") {
      return [];
    }
    const path = "/v2/positions/margined";
    try {
      const headers = this.signRequest("GET", path);
      const resp = await this.http.get(path, { headers });
      const results: any[] = resp.data?.result || [];
      return results.filter((p) => Math.abs(parseFloat(p.size || "0")) > 0);
    } catch (err: any) {
      console.warn(`Error fetching Delta positions: ${err.message}`);
      return [];
    }
  }

  /**
   * Close an open position on Delta Exchange by sending an opposing market order.
   */
  public async closePosition(productSymbol: string, size: number): Promise<{ success: boolean; message: string }> {
    if (!this.apiKey || !this.apiSecret) {
      return { success: false, message: "Delta credentials missing" };
    }
    const path = "/v2/orders";
    // If short (size < 0), buy back. If long (size > 0), sell.
    const side = size < 0 ? "buy" : "sell";
    const absContracts = Math.abs(size);
    const body = {
      product_symbol: productSymbol,
      size: absContracts,
      side,
      order_type: "market_order",
      reduce_only: true
    };
    const bodyStr = JSON.stringify(body);
    try {
      const headers = this.signRequest("POST", path, "", bodyStr);
      headers["Content-Type"] = "application/json";
      const resp = await this.http.post(path, body, { headers });
      if (resp.status === 200 || resp.status === 201) {
        return { success: true, message: `Closed ${absContracts} contracts of ${productSymbol} (${side})` };
      }
      return { success: false, message: `Failed: ${JSON.stringify(resp.data)}` };
    } catch (err: any) {
      return { success: false, message: err.response?.data?.error?.message || err.message };
    }
  }

  /**
   * Close all active positions and cancel open orders on Delta Exchange.
   */
  public async closeAllPositions(): Promise<{ closedCount: number; details: string[] }> {
    const positions = await this.getOpenPositions();
    const details: string[] = [];
    let closedCount = 0;

    for (const p of positions) {
      const sym = p.product_symbol || p.symbol;
      const size = parseFloat(p.size || "0");
      if (Math.abs(size) > 0) {
        const res = await this.closePosition(sym, size);
        details.push(`${sym}: ${res.message}`);
        if (res.success) closedCount++;
      }
    }

    return { closedCount, details };
  }

  public async getWalletBalance(): Promise<WalletBalanceInfo> {
    if (!this.apiKey || !this.apiSecret || this.apiKey === "your_delta_api_key_here") {
      return {
        exchange: "Delta Exchange",
        balanceUsd: 0,
        balanceInr: 0,
        availableUsd: 0,
        availableInr: 0,
        error: "Delta API credentials not set in .env"
      };
    }

    const path = "/v2/wallet/balances";
    try {
      const headers = this.signRequest("GET", path);
      const resp = await this.http.get(path, { headers });
      if (resp.status === 200) {
        const results: any[] = resp.data?.result || [];
        let totalUsd = 0;
        let availableUsd = 0;
        let spotPrice = 0;

        for (const item of results) {
          const asset = item.asset_symbol;
          const balance = parseFloat(item.balance || "0");
          const available = parseFloat(item.available_balance || item.balance || "0");

          if (["USDT", "USD", "USDC"].includes(asset)) {
            totalUsd += balance;
            availableUsd += available;
          } else if (asset === "BTC") {
            if (spotPrice === 0) {
              spotPrice = await this.getSpotPrice();
            }
            totalUsd += balance * spotPrice;
            availableUsd += available * spotPrice;
          }
        }

        return {
          exchange: "Delta Exchange",
          balanceUsd: Math.round(totalUsd * 100) / 100,
          balanceInr: Math.round(totalUsd * Config.USD_INR_RATE * 100) / 100,
          availableUsd: Math.round(availableUsd * 100) / 100,
          availableInr: Math.round(availableUsd * Config.USD_INR_RATE * 100) / 100,
          details: results
        };
      }
    } catch (err: any) {
      return {
        exchange: "Delta Exchange",
        balanceUsd: 0,
        balanceInr: 0,
        availableUsd: 0,
        availableInr: 0,
        error: `Delta API error: ${err.response?.data?.error?.message || err.message}`
      };
    }

    return {
      exchange: "Delta Exchange",
      balanceUsd: 0,
      balanceInr: 0,
      availableUsd: 0,
      availableInr: 0
    };
  }
}
