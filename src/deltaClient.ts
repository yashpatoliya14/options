/**
 * Delta Exchange API Client (TypeScript / Node.js).
 * Handles option chains (5:30 PM IST / 12:00 UTC settlement), spot price,
 * and authenticated HMAC-SHA256 wallet balances.
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

      // IST Date (UTC + 5:30)
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

  public async getWalletBalance(): Promise<WalletBalanceInfo> {
    if (
      !this.apiKey ||
      !this.apiSecret ||
      this.apiKey === "your_delta_api_key_here"
    ) {
      return {
        exchange: "Delta Exchange",
        isSimulated: true,
        balanceUsd: Config.SIMULATED_DELTA_BALANCE_USD,
        balanceInr: Math.round(Config.SIMULATED_DELTA_BALANCE_USD * Config.USD_INR_RATE * 100) / 100,
        details: "Simulated balance (Configure DELTA_API_KEY and DELTA_API_SECRET in .env for live balance)"
      };
    }

    const path = "/v2/wallet/balances";
    try {
      const headers = this.signRequest("GET", path);
      const resp = await this.http.get(path, { headers });
      if (resp.status === 200) {
        const results: any[] = resp.data?.result || [];
        let totalUsd = 0;
        let spotPrice = 0;

        for (const item of results) {
          const asset = item.asset_symbol;
          const balance = parseFloat(item.balance || "0");
          if (["USDT", "USD", "USDC"].includes(asset)) {
            totalUsd += balance;
          } else if (asset === "BTC") {
            if (spotPrice === 0) {
              spotPrice = await this.getSpotPrice();
            }
            totalUsd += balance * spotPrice;
          }
        }

        return {
          exchange: "Delta Exchange",
          isSimulated: false,
          balanceUsd: Math.round(totalUsd * 100) / 100,
          balanceInr: Math.round(totalUsd * Config.USD_INR_RATE * 100) / 100,
          details: results
        };
      }
    } catch (err: any) {
      console.warn(`Delta balance API error: ${err.message}`);
    }

    return {
      exchange: "Delta Exchange",
      isSimulated: true,
      balanceUsd: Config.SIMULATED_DELTA_BALANCE_USD,
      balanceInr: Math.round(Config.SIMULATED_DELTA_BALANCE_USD * Config.USD_INR_RATE * 100) / 100,
      details: "Using simulated balance fallback"
    };
  }
}

