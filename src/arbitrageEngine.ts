/**
 * Arbitrage Engine (LIVE ONLY).
 * Compares live options chains across Delta Exchange and CoinDCX.
 * Strategy:
 *  - LONG (BUY) on Delta Exchange (10 lots = 0.1 unit)
 *  - SHORT (SELL) on CoinDCX (1 lot = 0.1 unit)
 *  - Suggests strikes where CoinDCX premium >= Delta premium.
 *  - Computes Live Net PnL and Live Wallet Balances.
 */

import { Config, ASSETS, AssetConfig } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinDCXClient } from "./coindcxClient";
import {
  ParsedOption,
  StrikeComparison,
  OptionChainResult,
  OptionType,
  TradeExecution,
  NetPnlReport,
  CombinedWalletReport,
} from "./types";

export class ArbitrageEngine {
  public deltaClient: DeltaClient;
  public coindcxClient: CoinDCXClient;
  public activeTrades: TradeExecution[] = [];

  constructor() {
    this.deltaClient = new DeltaClient();
    this.coindcxClient = new CoinDCXClient();
  }

  /**
   * Get today's and tomorrow's dates in IST (YYYY-MM-DD).
   */
  public getExpiryDates(): { today: string; tomorrow: string; dates: string[] } {
    const nowUtc = new Date();
    const istNow = new Date(nowUtc.getTime() + 5.5 * 3600 * 1000);
    const today = istNow.toISOString().split("T")[0];

    const tomorrowIst = new Date(istNow.getTime() + 24 * 3600 * 1000);
    const tomorrow = tomorrowIst.toISOString().split("T")[0];

    return { today, tomorrow, dates: [today, tomorrow] };
  }

  /**
   * Scan options for a given asset. Returns ATM + OTM comparisons.
   */
  public async scanOptions(assetKey: string, expiryFilter?: string): Promise<OptionChainResult[]> {
    const asset = ASSETS[assetKey];
    if (!asset) throw new Error(`Unknown asset: ${assetKey}`);

    const { dates } = this.getExpiryDates();
    const targetDates = expiryFilter ? [expiryFilter] : dates;

    // Fetch live spot price
    const spotPrice = await this.deltaClient.getSpotPrice(asset.deltaUnderlying);
    if (spotPrice <= 0) throw new Error(`Could not fetch live spot price for ${asset.symbol}`);

    // Fetch live options from both exchanges
    const [deltaOpts, coindcxOpts] = await Promise.all([
      this.deltaClient.getOptions(asset, targetDates),
      this.coindcxClient.getOptions(asset, targetDates),
    ]);

    // Group options by expiry date
    const expiryGroups = new Map<string, { delta: ParsedOption[]; coindcx: ParsedOption[] }>();
    for (const date of targetDates) {
      expiryGroups.set(date, { delta: [], coindcx: [] });
    }
    for (const o of deltaOpts) {
      const group = expiryGroups.get(o.expiryDate);
      if (group) group.delta.push(o);
    }
    for (const o of coindcxOpts) {
      const group = expiryGroups.get(o.expiryDate);
      if (group) group.coindcx.push(o);
    }

    const results: OptionChainResult[] = [];

    for (const [date, group] of expiryGroups) {
      if (group.delta.length === 0 && group.coindcx.length === 0) continue;

      const deltaMap = new Map<string, ParsedOption>();
      for (const o of group.delta) deltaMap.set(`${o.strike}_${o.optionType}`, o);

      const coindcxMap = new Map<string, ParsedOption>();
      for (const o of group.coindcx) coindcxMap.set(`${o.strike}_${o.optionType}`, o);

      const allStrikeSet = new Set<number>();
      for (const o of [...group.delta, ...group.coindcx]) allStrikeSet.add(o.strike);
      const allStrikes = Array.from(allStrikeSet).sort((a, b) => a - b);

      // Identify ATM Strike
      let atmStrike = 0;
      let minDiff = Infinity;
      for (const s of allStrikes) {
        const d = Math.abs(s - spotPrice);
        if (d < minDiff) {
          minDiff = d;
          atmStrike = s;
        }
      }

      // OTM Calls (strikes > ATM) and OTM Puts (strikes < ATM)
      const otmCallStrikes = allStrikes.filter((s) => s > atmStrike).slice(0, Config.NUM_OTM_STRIKES);
      const otmPutStrikes = allStrikes
        .filter((s) => s < atmStrike)
        .sort((a, b) => b - a)
        .slice(0, Config.NUM_OTM_STRIKES);

      const buildComparison = (strike: number, optType: OptionType, category: "ATM" | "OTM"): StrikeComparison | null => {
        const key = `${strike}_${optType}`;
        const dOpt = deltaMap.get(key);
        const cOpt = coindcxMap.get(key);

        if (!dOpt && !cOpt) return null;

        const deltaPrice = dOpt?.markPrice || 0;
        const coindcxPrice = cOpt?.markPrice || 0;
        const spreadUsd = Math.round((coindcxPrice - deltaPrice) * 100) / 100;
        const spreadPct = deltaPrice > 0 ? Math.round(((spreadUsd / deltaPrice) * 100) * 10) / 10 : 0;

        // Requirement: Suggestion when Short Premium (CoinDCX) >= Long Premium (Delta)
        const isSuggested = coindcxPrice >= deltaPrice && coindcxPrice > 0 && deltaPrice > 0;

        // Lot sizing: 0.1 units (CoinDCX: 1 lot = 0.1, Delta: 10 lots = 0.1)
        const potentialProfitUsd = Math.round(spreadUsd * asset.lotSizeUnits * 100) / 100;
        const potentialProfitInr = Math.round(potentialProfitUsd * Config.USD_INR_RATE * 100) / 100;

        return {
          strike,
          optionType: optType,
          category,
          expiryDate: date,
          expiryLabel: dOpt?.expiryLabel || cOpt?.expiryLabel || date,
          deltaPrice,
          deltaBid: dOpt?.bestBid || 0,
          deltaAsk: dOpt?.bestAsk || 0,
          deltaIv: dOpt?.ivPct || 0,
          deltaSymbol: dOpt?.symbol || "",
          deltaProductId: dOpt?.productId,
          deltaLots: asset.deltaLots,
          coindcxPrice,
          coindcxBid: cOpt?.bestBid || 0,
          coindcxAsk: cOpt?.bestAsk || 0,
          coindcxIv: cOpt?.ivPct || 0,
          coindcxSymbol: cOpt?.symbol || "",
          coindcxLots: asset.coindcxLots,
          underlyingUnits: asset.lotSizeUnits,
          spreadUsd,
          spreadPct,
          potentialProfitUsd,
          potentialProfitInr,
          isSuggested,
        };
      };

      const atmCalls: StrikeComparison[] = [];
      const atmPuts: StrikeComparison[] = [];
      const otmCalls: StrikeComparison[] = [];
      const otmPuts: StrikeComparison[] = [];
      const allComparisons: StrikeComparison[] = [];

      const atmCall = buildComparison(atmStrike, "CALL", "ATM");
      const atmPut = buildComparison(atmStrike, "PUT", "ATM");
      if (atmCall) { atmCalls.push(atmCall); allComparisons.push(atmCall); }
      if (atmPut) { atmPuts.push(atmPut); allComparisons.push(atmPut); }

      for (const s of otmCallStrikes) {
        const comp = buildComparison(s, "CALL", "OTM");
        if (comp) { otmCalls.push(comp); allComparisons.push(comp); }
      }

      for (const s of otmPutStrikes) {
        const comp = buildComparison(s, "PUT", "OTM");
        if (comp) { otmPuts.push(comp); allComparisons.push(comp); }
      }

      const suggestedTrades = allComparisons
        .filter((c) => c.isSuggested)
        .sort((a, b) => b.spreadPct - a.spreadPct);

      results.push({
        underlying: asset.symbol,
        spotPrice,
        atmStrike,
        expiryDate: date,
        expiryLabel: group.delta.length > 0 ? "5:30 PM IST (Delta)" : "1:30 PM IST (CoinDCX)",
        atmCalls,
        atmPuts,
        otmCalls,
        otmPuts,
        allStrikes: allComparisons,
        suggestedTrades,
      });
    }

    return results;
  }

  /**
   * Execute LIVE Arbitrage Trade:
   *  - Delta Exchange: BUY (LONG) 10 lots (= 0.1 units)
   *  - CoinDCX: SELL (SHORT) 1 lot (= 0.1 units)
   */
  public async executeTrade(
    assetKey: string,
    strike: StrikeComparison
  ): Promise<TradeExecution> {
    const asset = ASSETS[assetKey];
    if (!asset) throw new Error(`Unknown asset: ${assetKey}`);

    const tradeId = `ARB-${Date.now()}`;
    const trade: TradeExecution = {
      id: tradeId,
      timestamp: new Date(),
      underlying: asset.symbol,
      strike: strike.strike,
      optionType: strike.optionType,
      expiryDate: strike.expiryDate,
      deltaSymbol: strike.deltaSymbol,
      deltaProductId: strike.deltaProductId,
      deltaSide: "buy",
      deltaLots: asset.deltaLots,
      coindcxSymbol: strike.coindcxSymbol,
      coindcxSide: "sell",
      coindcxLots: asset.coindcxLots,
      status: "pending",
    };

    try {
      const [deltaResult, coindcxResult] = await Promise.all([
        // Delta LIVE order: BUY 10 lots
        this.deltaClient.placeBuyOrder(
          strike.deltaProductId!,
          strike.deltaSymbol,
          asset.deltaLots
        ),
        // CoinDCX LIVE order: SELL 1 lot
        this.coindcxClient.placeSellOrder(
          strike.coindcxSymbol,
          asset.coindcxLots
        ),
      ]);

      trade.deltaOrderResult = deltaResult;
      trade.coindcxOrderResult = coindcxResult;

      if (deltaResult.success && coindcxResult.success) {
        trade.status = "filled";
      } else if (deltaResult.success || coindcxResult.success) {
        trade.status = "partial";
        trade.error = `Delta: ${deltaResult.message} | CoinDCX: ${coindcxResult.message}`;
      } else {
        trade.status = "failed";
        trade.error = `Delta: ${deltaResult.message} | CoinDCX: ${coindcxResult.message}`;
      }
    } catch (err: any) {
      trade.status = "failed";
      trade.error = err.message;
    }

    this.activeTrades.push(trade);
    return trade;
  }

  /**
   * Fetch LIVE Net PnL from open positions across both exchanges.
   */
  public async getLiveNetPnl(): Promise<NetPnlReport> {
    const [deltaPositions, coindcxPositions] = await Promise.all([
      this.deltaClient.getOpenPositions(),
      this.coindcxClient.getOpenPositions(),
    ]);

    let deltaUnrealizedUsd = 0;
    for (const p of deltaPositions) {
      deltaUnrealizedUsd += p.unrealizedPnlUsd;
    }

    let coindcxUnrealizedUsd = 0;
    for (const p of coindcxPositions) {
      coindcxUnrealizedUsd += p.unrealizedPnlUsd;
    }

    const totalUnrealizedUsd = Math.round((deltaUnrealizedUsd + coindcxUnrealizedUsd) * 100) / 100;
    const totalUnrealizedInr = Math.round(totalUnrealizedUsd * Config.USD_INR_RATE * 100) / 100;

    const deltaUnrealizedInr = Math.round(deltaUnrealizedUsd * Config.USD_INR_RATE * 100) / 100;
    const coindcxUnrealizedInr = Math.round(coindcxUnrealizedUsd * Config.USD_INR_RATE * 100) / 100;

    const totalPositionsCount = deltaPositions.length + coindcxPositions.length;
    const isFlat = totalPositionsCount === 0;

    return {
      timestamp: new Date(),
      deltaPositions,
      coindcxPositions,
      deltaUnrealizedUsd: Math.round(deltaUnrealizedUsd * 100) / 100,
      deltaUnrealizedInr,
      coindcxUnrealizedUsd: Math.round(coindcxUnrealizedUsd * 100) / 100,
      coindcxUnrealizedInr,
      totalUnrealizedUsd,
      totalUnrealizedInr,
      isFlat,
      totalPositionsCount,
    };
  }

  /**
   * Fetch LIVE Wallet Balances across both exchanges.
   */
  public async getLiveWalletBalances(): Promise<CombinedWalletReport> {
    const [delta, coindcx] = await Promise.all([
      this.deltaClient.getWalletBalance(),
      this.coindcxClient.getWalletBalance(),
    ]);

    const totalBalanceUsd = Math.round((delta.balanceUsd + coindcx.balanceUsd) * 100) / 100;
    const totalBalanceInr = Math.round(totalBalanceUsd * Config.USD_INR_RATE * 100) / 100;
    const totalAvailableUsd = Math.round((delta.availableUsd + coindcx.availableUsd) * 100) / 100;
    const totalAvailableInr = Math.round(totalAvailableUsd * Config.USD_INR_RATE * 100) / 100;

    return {
      timestamp: new Date(),
      delta,
      coindcx,
      totalBalanceUsd,
      totalBalanceInr,
      totalAvailableUsd,
      totalAvailableInr,
    };
  }

  /**
   * Close ALL positions on both exchanges.
   */
  public async closeAllPositions(): Promise<{
    delta: { closedCount: number; details: string[] };
    coindcx: { closedCount: number; details: string[] };
  }> {
    const [deltaClose, coindcxClose] = await Promise.all([
      this.deltaClient.closeAllPositions(),
      this.coindcxClient.closeAllPositions(),
    ]);

    for (const t of this.activeTrades) {
      if (t.status === "filled" || t.status === "partial") {
        t.status = "closed";
      }
    }

    return { delta: deltaClose, coindcx: coindcxClose };
  }
}
