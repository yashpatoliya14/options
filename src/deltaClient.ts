/**
 * Delta Exchange API Client (LIVE ONLY).
 * Handles:
 *  - Live options tickers & products
 *  - Live spot prices
 *  - Live wallet balances
 *  - Live open positions & Unrealized PnL
 *  - Live order placement (BUY / LONG)
 *  - Live position closing
 */

import crypto from "crypto";
import axios, { AxiosInstance } from "axios";
import { Config, AssetConfig } from "./config";
import { ParsedOption, OptionType, LivePosition, LiveWalletInfo } from "./types";

export class DeltaClient {
  private apiKey: string;
  private apiSecret: string;
  private baseUrl: string;
  private http: AxiosInstance;

  constructor() {
    this.apiKey = Config.DELTA_API_KEY;
    this.apiSecret = Config.DELTA_API_SECRET;
    this.baseUrl = Config.getDeltaBaseUrl().replace(/\/+$/, "");
    this.http = axios.create({
      baseURL: this.baseUrl,
      timeout: 15000,
      headers: {
        "User-Agent": "DeltaLiveClient/2.0",
        Accept: "application/json",
      },
    });
  }

  private sign(method: string, path: string, queryStr = "", payload = ""): Record<string, string> {
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const message = method.toUpperCase() + timestamp + path + queryStr + payload;
    const signature = crypto.createHmac("sha256", this.apiSecret).update(message).digest("hex");
    return {
      "api-key": this.apiKey,
      timestamp,
      signature,
    };
  }

  /**
   * Get live spot price for a given underlying.
   */
  public async getSpotPrice(underlying: string): Promise<number> {
    const symbol = underlying === "XAUT" ? "XAUTUSD" : `${underlying}USD`;
    try {
      const resp = await this.http.get(`/v2/tickers/${symbol}`);
      const mark = parseFloat(resp.data?.result?.mark_price || resp.data?.result?.spot_price || "0");
      if (mark > 0) return mark;
    } catch {
      // try fallback below
    }

    try {
      const sym2 = `${underlying}USDT`;
      const resp2 = await this.http.get(`/v2/tickers/${sym2}`);
      return parseFloat(resp2.data?.result?.mark_price || resp2.data?.result?.spot_price || "0");
    } catch (err: any) {
      console.error(`Delta spot price error for ${underlying}: ${err.message}`);
      return 0;
    }
  }

  /**
   * Fetch all live options products and tickers for a given underlying.
   * Filtered by target expiry dates.
   */
  public async getOptions(asset: AssetConfig, expiryDates: string[]): Promise<ParsedOption[]> {
    try {
      const [tickerResp, prodResp] = await Promise.all([
        this.http.get("/v2/tickers", {
          params: { contract_types: "call_options,put_options" },
        }),
        this.http.get("/v2/products", {
          params: { contract_types: "call_options,put_options", states: "live" },
        }),
      ]);

      const tickers: any[] = tickerResp.data?.result || [];
      const products: any[] = prodResp.data?.result || [];

      const prodMap = new Map<string, any>();
      for (const p of products) {
        if (p.symbol) prodMap.set(p.symbol, p);
      }

      const filtered = tickers.filter(
        (t) => t.underlying_asset_symbol === asset.deltaUnderlying
      );

      const parsed: ParsedOption[] = [];
      const nowUtc = new Date();
      const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);

      for (const t of filtered) {
        const sym = t.symbol || "";
        const prod = prodMap.get(sym) || {};
        const settleStr = prod.settlement_time || t.settlement_time;
        if (!settleStr) continue;

        const settleDtUtc = new Date(settleStr);
        if (isNaN(settleDtUtc.getTime()) || settleDtUtc < oneHourAgo) continue;

        // Convert to IST date
        const settleDtIst = new Date(settleDtUtc.getTime() + 5.5 * 3600 * 1000);
        const istDateStr = settleDtIst.toISOString().split("T")[0];

        if (!expiryDates.includes(istDateStr)) continue;

        const contractType = (t.contract_type || "").toLowerCase();
        const optionType: OptionType = contractType.includes("call") ? "CALL" : "PUT";
        const strike = parseFloat(t.strike_price || "0");
        if (isNaN(strike) || strike <= 0) continue;

        const quotes = t.quotes || {};
        const markPrice = parseFloat(t.mark_price || "0");
        const bestBid = parseFloat(quotes.best_bid || "0");
        const bestAsk = parseFloat(quotes.best_ask || "0");
        const markIv = parseFloat(quotes.mark_iv || t.mark_vol || "0") * 100;

        parsed.push({
          exchange: "Delta",
          symbol: sym,
          productId: prod.id || t.id,
          underlying: asset.symbol,
          strike,
          optionType,
          markPrice,
          bestBid,
          bestAsk,
          ivPct: Math.round(markIv * 100) / 100,
          expiryDate: istDateStr,
          expiryLabel: `${istDateStr} 5:30 PM IST`,
          settleDtUtc,
          raw: t,
        });
      }

      return parsed;
    } catch (err: any) {
      console.error(`Delta options fetch error: ${err.message}`);
      return [];
    }
  }

  /**
   * Fetch LIVE wallet balances directly from Delta Exchange.
   */
  public async getWalletBalance(): Promise<LiveWalletInfo> {
    if (!this.apiKey || !this.apiSecret) {
      return {
        exchange: "Delta Exchange",
        balanceUsd: 0,
        balanceInr: 0,
        availableUsd: 0,
        availableInr: 0,
        currencies: [],
        error: "Delta API credentials not set in .env",
      };
    }

    const path = "/v2/wallet/balances";
    try {
      const headers = this.sign("GET", path);
      const resp = await this.http.get(path, { headers });

      if (resp.status === 200) {
        const results: any[] = resp.data?.result || [];
        let totalUsd = 0;
        let availableUsd = 0;
        const currencies: { symbol: string; total: number; available: number }[] = [];

        // Fetch spot prices for BTC/ETH if needed
        let btcPrice = 0;

        for (const item of results) {
          const asset = item.asset_symbol || "";
          const bal = parseFloat(item.balance || "0");
          const avail = parseFloat(item.available_balance || item.balance || "0");

          if (bal === 0 && avail === 0) continue;

          currencies.push({ symbol: asset, total: bal, available: avail });

          if (["USDT", "USD", "USDC"].includes(asset)) {
            totalUsd += bal;
            availableUsd += avail;
          } else if (asset === "BTC") {
            if (btcPrice === 0) btcPrice = await this.getSpotPrice("BTC");
            totalUsd += bal * btcPrice;
            availableUsd += avail * btcPrice;
          }
        }

        return {
          exchange: "Delta Exchange",
          balanceUsd: Math.round(totalUsd * 100) / 100,
          balanceInr: Math.round(totalUsd * Config.USD_INR_RATE * 100) / 100,
          availableUsd: Math.round(availableUsd * 100) / 100,
          availableInr: Math.round(availableUsd * Config.USD_INR_RATE * 100) / 100,
          currencies,
        };
      }

      return {
        exchange: "Delta Exchange",
        balanceUsd: 0,
        balanceInr: 0,
        availableUsd: 0,
        availableInr: 0,
        currencies: [],
        error: `Delta API error: HTTP ${resp.status}`,
      };
    } catch (err: any) {
      return {
        exchange: "Delta Exchange",
        balanceUsd: 0,
        balanceInr: 0,
        availableUsd: 0,
        availableInr: 0,
        currencies: [],
        error: err.response?.data?.error?.message || err.message,
      };
    }
  }

  /**
   * Fetch LIVE open positions directly from Delta Exchange.
   */
  public async getOpenPositions(): Promise<LivePosition[]> {
    if (!this.apiKey || !this.apiSecret) return [];

    const path = "/v2/positions/margined";
    try {
      const headers = this.sign("GET", path);
      const resp = await this.http.get(path, { headers });
      const results: any[] = resp.data?.result || [];

      const positions: LivePosition[] = [];
      for (const p of results) {
        const size = parseFloat(p.size || "0");
        if (Math.abs(size) === 0) continue;

        const sym = p.product_symbol || p.symbol || "";
        const entryPrice = parseFloat(p.entry_price || "0");
        const markPrice = parseFloat(p.mark_price || "0");
        const unrealizedPnl = parseFloat(p.unrealized_pnl || "0");
        const side = size > 0 ? "LONG" : "SHORT";

        positions.push({
          exchange: "Delta Exchange",
          symbol: sym,
          side,
          size: Math.abs(size),
          entryPrice,
          markPrice,
          unrealizedPnlUsd: Math.round(unrealizedPnl * 100) / 100,
          unrealizedPnlInr: Math.round(unrealizedPnl * Config.USD_INR_RATE * 100) / 100,
          raw: p,
        });
      }

      return positions;
    } catch (err: any) {
      console.warn(`Delta positions error: ${err.message}`);
      return [];
    }
  }

  /**
   * Place a direct LIVE BUY order (LONG) on Delta Exchange.
   */
  public async placeBuyOrder(
    productId: number,
    productSymbol: string,
    contracts: number,
    price?: number
  ): Promise<{ success: boolean; message: string; data?: any }> {
    if (!this.apiKey || !this.apiSecret) {
      return { success: false, message: "Delta credentials missing in .env" };
    }

    const path = "/v2/orders";
    const body: any = {
      product_id: productId,
      size: contracts,
      side: "buy",
      order_type: "market_order",
    };

    if (price && price > 0) {
      body.order_type = "limit_order";
      body.limit_price = price.toString();
    }

    const bodyStr = JSON.stringify(body);

    try {
      const headers = this.sign("POST", path, "", bodyStr);
      headers["Content-Type"] = "application/json";
      const resp = await this.http.post(path, body, { headers });
      if (resp.status === 200 || resp.status === 201) {
        const orderId = resp.data?.result?.id || "N/A";
        return {
          success: true,
          message: `Delta BUY filled! Order ID: #${orderId} (${contracts} lots of ${productSymbol})`,
          data: resp.data?.result,
        };
      }
      return { success: false, message: `Delta order rejected: ${JSON.stringify(resp.data)}` };
    } catch (err: any) {
      const msg = err.response?.data?.error?.message || err.response?.data?.message || err.message;
      return { success: false, message: `Delta order failed: ${msg}` };
    }
  }

  /**
   * Close a single live position on Delta Exchange using reduce_only order.
   */
  public async closePosition(productSymbol: string, size: number): Promise<{ success: boolean; message: string }> {
    if (!this.apiKey || !this.apiSecret) {
      return { success: false, message: "Delta credentials missing" };
    }

    const path = "/v2/orders";
    const side = size < 0 ? "buy" : "sell";
    const absSize = Math.abs(size);
    const body = {
      product_symbol: productSymbol,
      size: absSize,
      side,
      order_type: "market_order",
      reduce_only: true,
    };

    const bodyStr = JSON.stringify(body);
    try {
      const headers = this.sign("POST", path, "", bodyStr);
      headers["Content-Type"] = "application/json";
      const resp = await this.http.post(path, body, { headers });
      if (resp.status === 200 || resp.status === 201) {
        return { success: true, message: `Closed ${absSize} lots of ${productSymbol} (${side})` };
      }
      return { success: false, message: `Close order failed: ${JSON.stringify(resp.data)}` };
    } catch (err: any) {
      return { success: false, message: err.response?.data?.error?.message || err.message };
    }
  }

  /**
   * Close ALL open positions on Delta Exchange.
   */
  public async closeAllPositions(): Promise<{ closedCount: number; details: string[] }> {
    const positions = await this.getOpenPositions();
    const details: string[] = [];
    let closedCount = 0;

    for (const p of positions) {
      const sym = p.symbol;
      const size = p.side === "SHORT" ? -p.size : p.size;
      if (Math.abs(size) > 0) {
        const res = await this.closePosition(sym, size);
        details.push(`${sym}: ${res.message}`);
        if (res.success) closedCount++;
      }
    }

    return { closedCount, details };
  }
}
