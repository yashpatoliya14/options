/**
 * Arbitrage Engine.
 * Compares options chains across Delta Exchange and CoinDCX.
 * Strategy: BUY (Long) on Delta + SELL (Short) on CoinDCX.
 * Suggests trades where CoinDCX premium >= Delta premium.
 */

import { Config, ASSETS, AssetConfig } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinDCXClient } from "./coindcxClient";
import { ParsedOption, StrikeComparison, OptionChainResult, OptionType, TradeExecution } from "./types";

export class ArbitrageEngine {
  public deltaClient: DeltaClient;
  public coindcxClient: CoinDCXClient;

  // Track active trades
  public activeTrades: TradeExecution[] = [];

  constructor() {
    this.deltaClient = new DeltaClient();
    this.coindcxClient = new CoinDCXClient();
  }

  /**
   * Get today and tomorrow's dates in IST (YYYY-MM-DD).
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

    // Fetch spot price
    const spotPrice = await this.deltaClient.getSpotPrice(asset.deltaUnderlying);
    if (spotPrice <= 0) throw new Error(`Could not fetch spot price for ${asset.symbol}`);

    // Fetch options from both exchanges
    const [deltaOpts, coindcxOpts] = await Promise.all([
      this.deltaClient.getOptions(asset, targetDates),
      this.coindcxClient.getOptions(asset, targetDates),
    ]);

    // Group by expiry date
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

      // Build strike maps
      const deltaMap = new Map<string, ParsedOption>();
      for (const o of group.delta) deltaMap.set(`${o.strike}_${o.optionType}`, o);

      const coindcxMap = new Map<string, ParsedOption>();
      for (const o of group.coindcx) coindcxMap.set(`${o.strike}_${o.optionType}`, o);

      // Find common strikes
      const allStrikeSet = new Set<number>();
      for (const o of [...group.delta, ...group.coindcx]) allStrikeSet.add(o.strike);
      const allStrikes = Array.from(allStrikeSet).sort((a, b) => a - b);

      // ATM strike
      let atmStrike = 0;
      let minDiff = Infinity;
      for (const s of allStrikes) {
        const d = Math.abs(s - spotPrice);
        if (d < minDiff) {
          minDiff = d;
          atmStrike = s;
        }
      }

      // OTM calls (above ATM) and OTM puts (below ATM)
      const otmCallStrikes = allStrikes.filter((s) => s > atmStrike).slice(0, Config.NUM_OTM_STRIKES);
      const otmPutStrikes = allStrikes.filter((s) => s < atmStrike).sort((a, b) => b - a).slice(0, Config.NUM_OTM_STRIKES);

      const buildComparison = (strike: number, optType: OptionType, category: "ATM" | "OTM"): StrikeComparison | null => {
        const key = `${strike}_${optType}`;
        const dOpt = deltaMap.get(key);
        const cOpt = coindcxMap.get(key);

        // Need at least one side
        if (!dOpt && !cOpt) return null;

        const deltaPrice = dOpt?.markPrice || 0;
        const coindcxPrice = cOpt?.markPrice || 0;
        const spreadUsd = Math.round((coindcxPrice - deltaPrice) * 100) / 100;
        const spreadPct = deltaPrice > 0 ? Math.round(((spreadUsd / deltaPrice) * 100) * 10) / 10 : 0;

        // Suggestion: CoinDCX (short) premium >= Delta (long) premium
        const isSuggested = coindcxPrice >= deltaPrice && coindcxPrice > 0 && deltaPrice > 0;

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
          coindcxPrice,
          coindcxBid: cOpt?.bestBid || 0,
          coindcxAsk: cOpt?.bestAsk || 0,
          coindcxIv: cOpt?.ivPct || 0,
          coindcxSymbol: cOpt?.symbol || "",
          spreadUsd,
          spreadPct,
          isSuggested,
        };
      };

      const atmCalls: StrikeComparison[] = [];
      const atmPuts: StrikeComparison[] = [];
      const otmCalls: StrikeComparison[] = [];
      const otmPuts: StrikeComparison[] = [];
      const allComparisons: StrikeComparison[] = [];

      // ATM
      const atmCall = buildComparison(atmStrike, "CALL", "ATM");
      const atmPut = buildComparison(atmStrike, "PUT", "ATM");
      if (atmCall) { atmCalls.push(atmCall); allComparisons.push(atmCall); }
      if (atmPut) { atmPuts.push(atmPut); allComparisons.push(atmPut); }

      // OTM Calls
      for (const s of otmCallStrikes) {
        const comp = buildComparison(s, "CALL", "OTM");
        if (comp) { otmCalls.push(comp); allComparisons.push(comp); }
      }

      // OTM Puts
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
   * Execute an arbitrage trade:
   * - BUY (Long) on Delta Exchange
   * - SELL (Short) on CoinDCX
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
      // Execute both legs
      const [deltaResult, coindcxResult] = await Promise.all([
        // Delta: BUY 10 lots (= 0.1 units)
        this.deltaClient.placeBuyOrder(
          strike.deltaProductId!,
          strike.deltaSymbol,
          asset.deltaLots
        ),
        // CoinDCX: SELL 1 lot (= 0.1 units)
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
   * Close all positions on both exchanges.
   */
  public async closeAllPositions(): Promise<{
    delta: { closedCount: number; details: string[] };
    coindcx: { closedCount: number; details: string[] };
  }> {
    const [deltaClose, coindcxClose] = await Promise.all([
      this.deltaClient.closeAllPositions(),
      this.coindcxClient.closeAllPositions(),
    ]);

    // Mark active trades as closed
    for (const t of this.activeTrades) {
      if (t.status === "filled" || t.status === "partial") {
        t.status = "closed";
      }
    }

    return { delta: deltaClose, coindcx: coindcxClose };
  }
}
