/**
 * CoinDCX API Client.
 * Handles fetching options chains, placing orders (SHORT), and closing positions.
 *
 * CoinDCX uses HMAC-SHA256 for signing.
 * Options are accessed via derivatives endpoints.
 */

import crypto from "crypto";
import axios, { AxiosInstance } from "axios";
import { Config, AssetConfig } from "./config";
import { ParsedOption, OptionType } from "./types";

export class CoinDCXClient {
  private apiKey: string;
  private apiSecret: string;
  private baseUrl: string;
  private http: AxiosInstance;

  constructor() {
    this.apiKey = Config.COINDCX_API_KEY;
    this.apiSecret = Config.COINDCX_API_SECRET;
    this.baseUrl = Config.COINDCX_BASE_URL.replace(/\/+$/, "");
    this.http = axios.create({
      baseURL: this.baseUrl,
      timeout: 15000,
      headers: {
        "Content-Type": "application/json",
      },
    });
  }

  /**
   * Sign a request body for CoinDCX authenticated endpoints.
   * CoinDCX uses HMAC-SHA256 on the JSON body string.
   */
  private sign(body: any): Record<string, string> {
    const bodyStr = JSON.stringify(body);
    const signature = crypto
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
   * Fetch options instruments available on CoinDCX for a given asset.
   * Since CoinDCX's options API may use the derivatives endpoint,
   * we query available instruments and filter by asset + options type.
   */
  public async getOptions(asset: AssetConfig, expiryDates: string[]): Promise<ParsedOption[]> {
    try {
      // Fetch derivatives market data to discover option instruments
      const resp = await this.http.get("/exchange/v1/derivatives/futures/data/instruments");
      const instruments: any[] = resp.data || [];

      // Filter for options on the target asset
      const optionInstruments = instruments.filter((inst: any) => {
        const pair = (inst.pair || inst.symbol || "").toUpperCase();
        const contractType = (inst.contract_type || inst.type || "").toLowerCase();
        return (
          pair.includes(asset.coindcxPrefix.toUpperCase()) &&
          (contractType.includes("option") || contractType.includes("call") || contractType.includes("put"))
        );
      });

      const parsed: ParsedOption[] = [];
      const nowUtc = new Date();
      const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);

      for (const inst of optionInstruments) {
        const expiry = inst.expiry || inst.settlement_time || inst.expiry_date;
        if (!expiry) continue;

        const expiryDt = new Date(expiry);
        if (isNaN(expiryDt.getTime()) || expiryDt < oneHourAgo) continue;

        const istDt = new Date(expiryDt.getTime() + 5.5 * 3600 * 1000);
        const istDateStr = istDt.toISOString().split("T")[0];
        if (!expiryDates.includes(istDateStr)) continue;

        const contractType = (inst.contract_type || inst.type || "").toLowerCase();
        const optionType: OptionType = contractType.includes("call") ? "CALL" : "PUT";

        const strike = parseFloat(inst.strike_price || inst.strike || "0");
        if (isNaN(strike) || strike <= 0) continue;

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

      // If no native CoinDCX options found, fallback to Deribit data
      // (CoinDCX sources liquidity from Deribit for crypto options)
      if (parsed.length === 0) {
        return this.getDeribitOptions(asset, expiryDates);
      }

      return parsed;
    } catch (err: any) {
      console.warn(`CoinDCX instruments fetch error: ${err.message}, falling back to Deribit`);
      return this.getDeribitOptions(asset, expiryDates);
    }
  }

  /**
   * Fallback: Fetch options data from Deribit (CoinDCX uses Deribit liquidity).
   */
  private async getDeribitOptions(asset: AssetConfig, expiryDates: string[]): Promise<ParsedOption[]> {
    const currency = asset.deltaUnderlying === "XAUT" ? "BTC" : asset.deltaUnderlying;
    const url = `https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=${currency}&kind=option`;

    try {
      const resp = await axios.get(url, { timeout: 15000 });
      const data: any[] = resp.data?.result || [];
      const parsed: ParsedOption[] = [];

      const months: Record<string, number> = {
        JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
        JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
      };

      // We need spot price to convert BTC-denominated prices to USD
      let spotPrice = 0;
      try {
        const spotResp = await axios.get(
          `https://www.deribit.com/api/v2/public/get_index_price?index_name=${currency.toLowerCase()}_usd`,
          { timeout: 10000 }
        );
        spotPrice = spotResp.data?.result?.index_price || 0;
      } catch {
        spotPrice = 0;
      }

      const nowUtc = new Date();
      const oneHourAgo = new Date(nowUtc.getTime() - 3600 * 1000);

      for (const item of data) {
        const instrument: string = item.instrument_name || "";
        const parts = instrument.split("-");
        if (parts.length < 4) continue;

        const [assetStr, expiryStr, strikeStr, optChar] = parts;
        if (assetStr !== currency) continue;

        const optionType: OptionType = optChar.toUpperCase() === "C" ? "CALL" : "PUT";
        const strike = parseFloat(strikeStr);
        if (isNaN(strike)) continue;

        const dayMatch = expiryStr.match(/^(\d{1,2})([A-Z]{3})(\d{2})$/);
        if (!dayMatch) continue;

        const day = parseInt(dayMatch[1], 10);
        const mon = months[dayMatch[2]];
        const year = 2000 + parseInt(dayMatch[3], 10);
        if (mon === undefined) continue;

        const settleDtUtc = new Date(Date.UTC(year, mon, day, 8, 0, 0)); // 08:00 UTC = 1:30 PM IST
        if (settleDtUtc < oneHourAgo) continue;

        const istDt = new Date(settleDtUtc.getTime() + 5.5 * 3600 * 1000);
        const istDateStr = istDt.toISOString().split("T")[0];
        if (!expiryDates.includes(istDateStr)) continue;

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
    } catch (err: any) {
      console.error(`Deribit fallback error: ${err.message}`);
      return [];
    }
  }

  /**
   * Place a SELL order (SHORT) on CoinDCX.
   */
  public async placeSellOrder(
    instrumentSymbol: string,
    quantity: number,
    price?: number
  ): Promise<{ success: boolean; message: string; data?: any }> {
    if (!this.apiKey || !this.apiSecret) {
      return { success: false, message: "CoinDCX credentials missing" };
    }

    const body: any = {
      timestamp: Date.now(),
      side: "sell",
      order_type: price ? "limit_order" : "market_order",
      pair: instrumentSymbol,
      total_quantity: quantity,
    };

    if (price) {
      body.price = price;
    }

    try {
      const headers = this.sign(body);
      const resp = await this.http.post(
        "/exchange/v1/derivatives/futures/orders/create",
        body,
        { headers }
      );
      if (resp.status === 200 || resp.status === 201) {
        return {
          success: true,
          message: `CoinDCX SELL ${quantity} lots of ${instrumentSymbol}`,
          data: resp.data,
        };
      }
      return { success: false, message: `CoinDCX order failed: ${JSON.stringify(resp.data)}` };
    } catch (err: any) {
      const msg = err.response?.data?.message || err.response?.data?.error || err.message;
      return { success: false, message: `CoinDCX order error: ${msg}` };
    }
  }

  /**
   * Get open positions on CoinDCX.
   */
  public async getOpenPositions(): Promise<any[]> {
    if (!this.apiKey || !this.apiSecret) return [];

    const body = { timestamp: Date.now() };
    try {
      const headers = this.sign(body);
      const resp = await this.http.post(
        "/exchange/v1/derivatives/futures/positions",
        body,
        { headers }
      );
      const positions: any[] = resp.data?.data || resp.data || [];
      return positions.filter((p) => {
        const qty = parseFloat(p.quantity || p.size || "0");
        return Math.abs(qty) > 0;
      });
    } catch (err: any) {
      console.warn(`CoinDCX positions error: ${err.message}`);
      return [];
    }
  }

  /**
   * Close a position on CoinDCX by placing an opposite order.
   */
  public async closePosition(
    symbol: string,
    quantity: number,
    currentSide: string
  ): Promise<{ success: boolean; message: string }> {
    if (!this.apiKey || !this.apiSecret) {
      return { success: false, message: "CoinDCX credentials missing" };
    }

    const side = currentSide.toLowerCase().includes("sell") ? "buy" : "sell";
    const body: any = {
      timestamp: Date.now(),
      side,
      order_type: "market_order",
      pair: symbol,
      total_quantity: Math.abs(quantity),
    };

    try {
      const headers = this.sign(body);
      const resp = await this.http.post(
        "/exchange/v1/derivatives/futures/orders/create",
        body,
        { headers }
      );
      if (resp.status === 200 || resp.status === 201) {
        return { success: true, message: `Closed ${Math.abs(quantity)} of ${symbol} (${side})` };
      }
      return { success: false, message: `Failed: ${JSON.stringify(resp.data)}` };
    } catch (err: any) {
      return { success: false, message: err.response?.data?.message || err.message };
    }
  }

  /**
   * Close ALL open positions on CoinDCX.
   */
  public async closeAllPositions(): Promise<{ closedCount: number; details: string[] }> {
    const positions = await this.getOpenPositions();
    const details: string[] = [];
    let closedCount = 0;

    for (const p of positions) {
      const sym = p.pair || p.symbol;
      const qty = parseFloat(p.quantity || p.size || "0");
      const side = p.side || "sell";
      if (Math.abs(qty) > 0) {
        const res = await this.closePosition(sym, qty, side);
        details.push(`${sym}: ${res.message}`);
        if (res.success) closedCount++;
      }
    }

    return { closedCount, details };
  }
}

