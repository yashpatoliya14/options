/**
 * Arbitrage Engine (LIVE ONLY).
 * Compares live options chains across Delta Exchange and CoinSwitch PRO.
 *
 * Strategy:
 *  - LONG (BUY) on Delta Exchange (10 lots = 0.1 unit)
 *  - SHORT (SELL) on CoinSwitch PRO (1 lot = 0.1 unit)
 *  - Deducts fees for BOTH Delta Exchange and CoinSwitch PRO.
 *  - Arbitrage Green Mark (🟢): Highlighted ONLY when Net Profit > 0 after all fees.
 *  - Computes Combined Live Net PnL and Live Wallet Balances.
 *  - Automated Daily Cutoff at 1:30 PM IST.
 */

import { Config, ASSETS, AssetConfig } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinSwitchClient } from "./coinSwitchClient";
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
  public coinswitchClient: CoinSwitchClient;
  public activeTrades: TradeExecution[] = [];

  constructor() {
    this.deltaClient = new DeltaClient();
    this.coinswitchClient = new CoinSwitchClient();
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
   * Scan options for a given asset. Returns ATM + OTM comparisons with fee deduction.
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
    const [deltaOpts, coinswitchOpts] = await Promise.all([
      this.deltaClient.getOptions(asset, targetDates),
      this.coinswitchClient.getOptions(asset, targetDates),
    ]);

    // Group options by expiry date
    const expiryGroups = new Map<string, { delta: ParsedOption[]; coinswitch: ParsedOption[] }>();
    for (const date of targetDates) {
      expiryGroups.set(date, { delta: [], coinswitch: [] });
    }
    for (const o of deltaOpts) {
      const group = expiryGroups.get(o.expiryDate);
      if (group) group.delta.push(o);
    }
    for (const o of coinswitchOpts) {
      const group = expiryGroups.get(o.expiryDate);
      if (group) group.coinswitch.push(o);
    }

    const results: OptionChainResult[] = [];

    for (const [date, group] of expiryGroups) {
      if (group.delta.length === 0 && group.coinswitch.length === 0) continue;

      const deltaMap = new Map<string, ParsedOption>();
      for (const o of group.delta) deltaMap.set(`${o.strike}_${o.optionType}`, o);

      const coinswitchMap = new Map<string, ParsedOption>();
      for (const o of group.coinswitch) coinswitchMap.set(`${o.strike}_${o.optionType}`, o);

      const deltaStrikes = new Set(group.delta.map((o) => o.strike));
      const coinswitchStrikes = new Set(group.coinswitch.map((o) => o.strike));
      const commonStrikes = Array.from(deltaStrikes)
        .filter((s) => coinswitchStrikes.has(s))
        .sort((a, b) => a - b);

      const allStrikeSet = new Set<number>();
      for (const o of [...group.delta, ...group.coinswitch]) allStrikeSet.add(o.strike);
      const allStrikes = commonStrikes.length >= 2
        ? commonStrikes
        : Array.from(allStrikeSet).sort((a, b) => a - b);

      // Identify ATM Strike (closest strike to spot)
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

      const buildComparison = (
        strike: number,
        optType: OptionType,
        category: "ATM" | "OTM"
      ): StrikeComparison | null => {
        const key = `${strike}_${optType}`;
        const dOpt = deltaMap.get(key);
        const cOpt = coinswitchMap.get(key);

        if (!dOpt && !cOpt) return null;

        const deltaPrice = dOpt?.markPrice || 0;
        const coinswitchPrice = cOpt?.markPrice || 0;
        const spreadUsd = Math.round((coinswitchPrice - deltaPrice) * 100) / 100;
        const spreadPct = deltaPrice > 0 ? Math.round(((spreadUsd / deltaPrice) * 100) * 10) / 10 : 0;

        // 1. Gross Profit before fees
        // 0.1 units notional (CoinSwitch 1 lot = 0.1, Delta 10 lots = 0.1)
        const grossProfitUsd = Math.round(spreadUsd * asset.lotSizeUnits * 100) / 100;
        const grossProfitInr = Math.round(grossProfitUsd * Config.USD_INR_RATE * 100) / 100;

        // 2. Fee Deductions for Delta Exchange (Long Leg: BUY 0.1 unit)
        // Delta Exchange rule: 0.05% of notional (spot * 0.1), capped at 10% of premium
        const deltaNotionalUsd = spotPrice * asset.lotSizeUnits;
        const deltaPremiumTotalUsd = deltaPrice * asset.lotSizeUnits;
        const deltaFeeNotionalUsd = deltaNotionalUsd * (Config.DELTA_FEE_PCT / 100);
        const deltaFeeCapUsd = deltaPremiumTotalUsd * (Config.MAX_PREMIUM_FEE_CAP_PCT / 100);
        const deltaFeeUsd = deltaPrice > 0
          ? Math.round(Math.min(deltaFeeNotionalUsd, deltaFeeCapUsd) * 100) / 100
          : 0;
        const deltaFeeInr = Math.round(deltaFeeUsd * Config.USD_INR_RATE * 100) / 100;

        // 3. Fee Deductions for CoinSwitch PRO (Short Leg: SELL 0.1 unit)
        // CoinSwitch PRO rule: 0.05% of notional, capped at 10% of premium
        const coinswitchNotionalUsd = spotPrice * asset.lotSizeUnits;
        const coinswitchPremiumTotalUsd = coinswitchPrice * asset.lotSizeUnits;
        const coinswitchFeeNotionalUsd = coinswitchNotionalUsd * (Config.COINSWITCH_FEE_PCT / 100);
        const coinswitchFeeCapUsd = coinswitchPremiumTotalUsd * (Config.MAX_PREMIUM_FEE_CAP_PCT / 100);
        const coinswitchFeeUsd = coinswitchPrice > 0
          ? Math.round(Math.min(coinswitchFeeNotionalUsd, coinswitchFeeCapUsd) * 100) / 100
          : 0;
        const coinswitchFeeInr = Math.round(coinswitchFeeUsd * Config.USD_INR_RATE * 100) / 100;

        // 4. Total Combined Fees
        const totalFeesUsd = Math.round((deltaFeeUsd + coinswitchFeeUsd) * 100) / 100;
        const totalFeesInr = Math.round(totalFeesUsd * Config.USD_INR_RATE * 100) / 100;

        // 5. Net Profit After Deducting All Fees
        const netProfitUsd = Math.round((grossProfitUsd - totalFeesUsd) * 100) / 100;
        const netProfitInr = Math.round(netProfitUsd * Config.USD_INR_RATE * 100) / 100;

        // 6. Arbitrage Green Mark Condition:
        // ONLY marked green (🟢) if Net Profit > 0 after deducting all fees for both exchanges!
        const isArb = netProfitUsd > 0 && coinswitchPrice > deltaPrice && coinswitchPrice > 0 && deltaPrice > 0;

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
          coinswitchPrice,
          coinswitchBid: cOpt?.bestBid || 0,
          coinswitchAsk: cOpt?.bestAsk || 0,
          coinswitchIv: cOpt?.ivPct || 0,
          coinswitchSymbol: cOpt?.symbol || "",
          coinswitchLots: asset.coinswitchLots,
          underlyingUnits: asset.lotSizeUnits,
          spreadUsd,
          spreadPct,
          grossProfitUsd,
          grossProfitInr,
          deltaFeeUsd,
          deltaFeeInr,
          coinswitchFeeUsd,
          coinswitchFeeInr,
          totalFeesUsd,
          totalFeesInr,
          netProfitUsd,
          netProfitInr,
          isArb,
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

      // Filter only true green mark arbitrage opportunities (Net Profit > 0 after fees)
      const arbOpportunities = allComparisons
        .filter((c) => c.isArb)
        .sort((a, b) => b.netProfitUsd - a.netProfitUsd);

      results.push({
        underlying: asset.symbol,
        spotPrice,
        atmStrike,
        expiryDate: date,
        expiryLabel: `${date} 1:30 PM IST (Cutoff)`,
        atmCalls,
        atmPuts,
        otmCalls,
        otmPuts,
        allStrikes: allComparisons,
        arbOpportunities,
      });
    }

    return results;
  }

  /**
   * Execute LIVE Arbitrage Trade:
   *  - Delta Exchange: BUY (LONG) 10 lots (= 0.1 units)
   *  - CoinSwitch PRO: SELL (SHORT) 1 lot (= 0.1 units)
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
      coinswitchSymbol: strike.coinswitchSymbol,
      coinswitchSide: "sell",
      coinswitchLots: asset.coinswitchLots,
      grossProfitUsd: strike.grossProfitUsd,
      totalFeesUsd: strike.totalFeesUsd,
      netProfitUsd: strike.netProfitUsd,
      netProfitInr: strike.netProfitInr,
      status: "pending",
    };

    try {
      const [deltaResult, coinswitchResult] = await Promise.all([
        // Delta LIVE order: BUY 10 lots
        this.deltaClient.placeBuyOrder(
          strike.deltaProductId!,
          strike.deltaSymbol,
          asset.deltaLots
        ),
        // CoinSwitch LIVE order: SELL 1 lot
        this.coinswitchClient.placeSellOrder(
          strike.coinswitchSymbol,
          asset.coinswitchLots
        ),
      ]);

      trade.deltaOrderResult = deltaResult;
      trade.coinswitchOrderResult = coinswitchResult;

      if (deltaResult.success && coinswitchResult.success) {
        trade.status = "filled";
      } else if (deltaResult.success || coinswitchResult.success) {
        trade.status = "partial";
        trade.error = `Delta: ${deltaResult.message} | CoinSwitch: ${coinswitchResult.message}`;
      } else {
        trade.status = "failed";
        trade.error = `Delta: ${deltaResult.message} | CoinSwitch: ${coinswitchResult.message}`;
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
    const [deltaPositions, coinswitchPositions] = await Promise.all([
      this.deltaClient.getOpenPositions(),
      this.coinswitchClient.getOpenPositions(),
    ]);

    let deltaUnrealizedUsd = 0;
    for (const p of deltaPositions) {
      deltaUnrealizedUsd += p.unrealizedPnlUsd;
    }

    let coinswitchUnrealizedUsd = 0;
    for (const p of coinswitchPositions) {
      coinswitchUnrealizedUsd += p.unrealizedPnlUsd;
    }

    const totalUnrealizedUsd = Math.round((deltaUnrealizedUsd + coinswitchUnrealizedUsd) * 100) / 100;
    const totalUnrealizedInr = Math.round(totalUnrealizedUsd * Config.USD_INR_RATE * 100) / 100;

    const deltaUnrealizedInr = Math.round(deltaUnrealizedUsd * Config.USD_INR_RATE * 100) / 100;
    const coinswitchUnrealizedInr = Math.round(coinswitchUnrealizedUsd * Config.USD_INR_RATE * 100) / 100;

    const totalPositionsCount = deltaPositions.length + coinswitchPositions.length;
    const isFlat = totalPositionsCount === 0;

    return {
      timestamp: new Date(),
      deltaPositions,
      coinswitchPositions,
      deltaUnrealizedUsd: Math.round(deltaUnrealizedUsd * 100) / 100,
      deltaUnrealizedInr,
      coinswitchUnrealizedUsd: Math.round(coinswitchUnrealizedUsd * 100) / 100,
      coinswitchUnrealizedInr,
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
    const [delta, coinswitch] = await Promise.all([
      this.deltaClient.getWalletBalance(),
      this.coinswitchClient.getWalletBalance(),
    ]);

    const totalBalanceUsd = Math.round((delta.balanceUsd + coinswitch.balanceUsd) * 100) / 100;
    const totalBalanceInr = Math.round(totalBalanceUsd * Config.USD_INR_RATE * 100) / 100;
    const totalAvailableUsd = Math.round((delta.availableUsd + coinswitch.availableUsd) * 100) / 100;
    const totalAvailableInr = Math.round(totalAvailableUsd * Config.USD_INR_RATE * 100) / 100;

    return {
      timestamp: new Date(),
      delta,
      coinswitch,
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
    coinswitch: { closedCount: number; details: string[] };
  }> {
    const [deltaClose, coinswitchClose] = await Promise.all([
      this.deltaClient.closeAllPositions(),
      this.coinswitchClient.closeAllPositions(),
    ]);

    for (const t of this.activeTrades) {
      if (t.status === "filled" || t.status === "partial") {
        t.status = "closed";
      }
    }

    return { delta: deltaClose, coinswitch: coinswitchClose };
  }
}
