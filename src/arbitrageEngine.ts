/**
 * Arbitrage Engine (TypeScript / Node.js).
 * Auto-adjusts position sizing and margin based on global Config.TRADE_LOT_BTC.
 */

import { Config } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinSwitchClient } from "./coinSwitchClient";
import { ArbitrageRow, ScanResult, OptionType } from "./types";

export class ArbitrageEngine {
  public deltaClient: DeltaClient;
  public coinSwitchClient: CoinSwitchClient;

  constructor(deltaClient?: DeltaClient, coinSwitchClient?: CoinSwitchClient) {
    this.deltaClient = deltaClient || new DeltaClient();
    this.coinSwitchClient = coinSwitchClient || new CoinSwitchClient();
  }

  public getTargetExpiryDate(): { targetDateStr: string; cycleDesc: string } {
    const nowUtc = new Date();
    const istNow = new Date(nowUtc.getTime() + 5.5 * 3600 * 1000);

    const istHour = istNow.getUTCHours();
    const istMin = istNow.getUTCMinutes();

    let targetIst = new Date(istNow);
    let cycleDesc = "";

    if (istHour > 13 || (istHour === 13 && istMin >= 30)) {
      targetIst = new Date(targetIst.getTime() + 24 * 3600 * 1000);
      cycleDesc = `Next Day Expiry (${targetIst.toISOString().split("T")[0]})`;
    } else {
      cycleDesc = `Same Day 0DTE Expiry (${targetIst.toISOString().split("T")[0]})`;
    }

    const targetDateStr = targetIst.toISOString().split("T")[0];
    return { targetDateStr, cycleDesc };
  }

  public async scanOptions(targetDate?: string): Promise<ScanResult> {
    const spotPrice = await this.deltaClient.getSpotPrice();
    let { targetDateStr, cycleDesc } = this.getTargetExpiryDate();

    if (targetDate) {
      targetDateStr = targetDate;
      cycleDesc = `Expiry (${targetDate})`;
    }

    let deltaOpts = await this.deltaClient.getParsedOptions(targetDateStr);
    let csOpts = await this.coinSwitchClient.getParsedOptions(spotPrice, targetDateStr);

    if (deltaOpts.length === 0 || csOpts.length === 0) {
      const [deltaAll, csAll] = await Promise.all([
        this.deltaClient.getParsedOptions(),
        this.coinSwitchClient.getParsedOptions(spotPrice)
      ]);

      const deltaDates = new Set(deltaAll.map((o) => o.settleDtIst.toISOString().split("T")[0]));
      const csDates = new Set(csAll.map((o) => o.settleDtIst.toISOString().split("T")[0]));
      const commonDates = Array.from(deltaDates).filter((d) => csDates.has(d)).sort();

      if (commonDates.length > 0) {
        targetDateStr = commonDates[0];
        cycleDesc = `Earliest Common Expiry (${targetDateStr})`;
        deltaOpts = deltaAll.filter((o) => o.settleDtIst.toISOString().split("T")[0] === targetDateStr);
        csOpts = csAll.filter((o) => o.settleDtIst.toISOString().split("T")[0] === targetDateStr);
      }
    }

    const deltaMap = new Map<string, any>();
    for (const o of deltaOpts) {
      deltaMap.set(`${o.strike}_${o.optionType}`, o);
    }

    const csMap = new Map<string, any>();
    for (const o of csOpts) {
      csMap.set(`${o.strike}_${o.optionType}`, o);
    }

    // Common strikes across both exchanges
    const deltaStrikes = new Set(deltaOpts.map((o) => o.strike));
    const csStrikes = new Set(csOpts.map((o) => o.strike));
    const commonStrikes = Array.from(deltaStrikes).filter((s) => csStrikes.has(s)).sort((a, b) => a - b);

    const strikeSet = new Set<number>();
    for (const o of [...deltaOpts, ...csOpts]) {
      strikeSet.add(o.strike);
    }
    const allStrikes = commonStrikes.length >= 3 
      ? commonStrikes 
      : Array.from(strikeSet).sort((a, b) => a - b);

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

    const otmCallStrikes = allStrikes
      .filter((s) => s > atmStrike)
      .slice(0, Config.NUM_OTM_STRIKES);

    const otmPutStrikes = allStrikes
      .filter((s) => s < atmStrike)
      .sort((a, b) => b - a)
      .slice(0, Config.NUM_OTM_STRIKES);

    // Sizing formulas:
    // Global TRADE_LOT_BTC (e.g. 0.05 BTC)
    // 1 Delta BTC option contract = 0.001 BTC => contracts = TRADE_LOT_BTC / 0.001
    const tradeLotBtc = Config.TRADE_LOT_BTC;
    const deltaContracts = Math.round(tradeLotBtc / 0.001);

    const buildRow = (strike: number, optType: OptionType, category: "ATM" | "OTM Call" | "OTM Put"): ArbitrageRow | null => {
      const key = `${strike}_${optType}`;
      const dOpt = deltaMap.get(key);
      const cOpt = csMap.get(key);

      if (!dOpt && !cOpt) return null;

      const dMark = dOpt ? dOpt.markPrice : 0;
      const cMark = cOpt ? cOpt.markPrice : 0;

      const dIv = dOpt ? dOpt.ivPct : 0;
      const cIv = cOpt ? cOpt.ivPct : 0;

      const diff = Math.round((cMark - dMark) * 100) / 100;
      const diffPct = dMark > 0 ? Math.round(((diff / dMark) * 100) * 10) / 10 : 0;
      const ivDiff = Math.round((cIv - dIv) * 10) / 10;

      const isArbitrage = cMark >= dMark && cMark > 0 && dMark > 0;

      // Equal Margin calculations across both exchanges
      // Standard short option initial margin is approx 10% to 15% of spot notional
      const notionalUsd = tradeLotBtc * spotPrice;
      const requiredMarginUsd = Math.round(notionalUsd * 0.12 * 100) / 100; // ~12% margin
      const requiredMarginInr = Math.round(requiredMarginUsd * Config.USD_INR_RATE * 100) / 100;

      // Potential net arbitrage profit for the configured lot size
      const potentialProfitUsd = Math.round(diff * tradeLotBtc * 100) / 100;
      const potentialProfitInr = Math.round(potentialProfitUsd * Config.USD_INR_RATE * 100) / 100;

      return {
        strike,
        optionType: optType,
        category,
        coinswitchPrice: cMark,
        coinswitchPriceInr: Math.round(cMark * Config.USD_INR_RATE * 100) / 100,
        coinswitchBid: cOpt ? cOpt.bestBid : 0,
        coinswitchAsk: cOpt ? cOpt.bestAsk : 0,
        coinswitchIv: cIv,
        deltaPrice: dMark,
        deltaPriceInr: Math.round(dMark * Config.USD_INR_RATE * 100) / 100,
        deltaBid: dOpt ? dOpt.bestBid : 0,
        deltaAsk: dOpt ? dOpt.bestAsk : 0,
        deltaIv: dIv,
        diff,
        diffPct,
        ivDiff,
        isArbitrage,
        targetDateStr,
        deltaExpiry: "5:30 PM IST",
        coinswitchExpiry: "1:30 PM IST",
        tradeLotBtc,
        deltaContracts,
        requiredMarginUsd,
        requiredMarginInr,
        potentialProfitUsd,
        potentialProfitInr
      };
    };

    const atmCall = buildRow(atmStrike, "CALL", "ATM") || undefined;
    const atmPut = buildRow(atmStrike, "PUT", "ATM") || undefined;

    const otmCalls = otmCallStrikes
      .map((s) => buildRow(s, "CALL", "OTM Call"))
      .filter((r): r is ArbitrageRow => r !== null);

    const otmPuts = otmPutStrikes
      .map((s) => buildRow(s, "PUT", "OTM Put"))
      .filter((r): r is ArbitrageRow => r !== null);

    const allRows: ArbitrageRow[] = [];
    if (atmCall) allRows.push(atmCall);
    if (atmPut) allRows.push(atmPut);
    allRows.push(...otmCalls, ...otmPuts);

    const arbitrageOpportunities = allRows
      .filter((r) => r.isArbitrage)
      .sort((a, b) => b.diffPct - a.diffPct);

    return {
      spotPrice,
      targetDate: new Date(targetDateStr),
      cycleDesc,
      atmStrike,
      atmCall,
      atmPut,
      otmCalls,
      otmPuts,
      allRows,
      arbitrageOpportunities,
      tradeLotBtc
    };
  }

  public formatTelegramReport(res: ScanResult): string {
    const spot = res.spotPrice;
    const lotBtc = res.tradeLotBtc;
    const deltaContracts = Math.round(lotBtc / 0.001);
    const lines: string[] = [];

    lines.push("⚡ *OPTIONS ARBITRAGE SCANNER (LIVE)* ⚡");
    lines.push("━━━━━━━━━━━━━━━━━━━━━━");
    lines.push(`🪙 *Underlying:* ${Config.UNDERLYING_ASSET} Spot: *$${spot.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}* (₹${Math.round(spot * Config.USD_INR_RATE).toLocaleString("en-IN")})`);
    lines.push(`📦 *Global Sizing:* *${lotBtc} BTC* (Delta: *${deltaContracts} contracts* | CoinSwitch: *${lotBtc} BTC*)`);
    lines.push(`📅 *Cycle:* ${res.cycleDesc}`);
    lines.push("⏰ *CoinSwitch Expiry:* 1:30 PM IST (08:00 UTC)");
    lines.push("⏰ *Delta Expiry:* 5:30 PM IST (12:00 UTC)");
    lines.push("━━━━━━━━━━━━━━━━━━━━━━\n");

    if (res.arbitrageOpportunities.length > 0) {
      lines.push("🔥 *LIVE ARBITRAGE SIGNALS (CoinSwitch ≥ Delta)* 🔥");
      lines.push("_CoinSwitch is overpriced despite expiring 4 hours earlier!_\n");
      for (const a of res.arbitrageOpportunities) {
        lines.push(
          `🟢 *${a.strike.toLocaleString()} ${a.optionType}* (${a.category})\n` +
          `   ├ CoinSwitch (1:30 PM): *$${a.coinswitchPrice.toFixed(2)}* (₹${a.coinswitchPriceInr.toLocaleString("en-IN")}) | IV: ${a.coinswitchIv}%\n` +
          `   ├ Delta (5:30 PM): *$${a.deltaPrice.toFixed(2)}* (₹${a.deltaPriceInr.toLocaleString("en-IN")}) | IV: ${a.deltaIv}%\n` +
          `   ├ 💰 *Spread:* +$${a.diff.toFixed(2)} (*+${a.diffPct}%*) | IV Edge: +${a.ivDiff}%\n` +
          `   ├ 📊 *Lot (${lotBtc} BTC):* Profit: *+$${a.potentialProfitUsd.toFixed(2)}* (₹${a.potentialProfitInr.toLocaleString("en-IN")}) | Margin: ~$${a.requiredMarginUsd.toFixed(0)}/ex\n` +
          `   └ 👉 *Trade:* SELL ${lotBtc} BTC CoinSwitch / BUY ${deltaContracts} contracts Delta\n`
        );
      }
    } else {
      lines.push("ℹ️ *No strict CoinSwitch ≥ Delta condition right now.* Closest spreads below:\n");
    }

    lines.push(`📊 *ATM OPTIONS (Strike ${res.atmStrike.toLocaleString()})*`);
    lines.push("----------------------------------------");
    if (res.atmCall) {
      const tag = res.atmCall.isArbitrage ? "🟢 ARB" : "⚪ Normal";
      lines.push(
        `🔹 *CALL:* ${tag}\n` +
        `   • CoinSwitch: $${res.atmCall.coinswitchPrice.toFixed(2)} (IV ${res.atmCall.coinswitchIv}%)\n` +
        `   • Delta: $${res.atmCall.deltaPrice.toFixed(2)} (IV ${res.atmCall.deltaIv}%)\n` +
        `   • Diff: $${res.atmCall.diff >= 0 ? "+" : ""}${res.atmCall.diff.toFixed(2)} (${res.atmCall.diffPct >= 0 ? "+" : ""}${res.atmCall.diffPct.toFixed(1)}%)`
      );
    }
    if (res.atmPut) {
      const tag = res.atmPut.isArbitrage ? "🟢 ARB" : "⚪ Normal";
      lines.push(
        `🔸 *PUT:* ${tag}\n` +
        `   • CoinSwitch: $${res.atmPut.coinswitchPrice.toFixed(2)} (IV ${res.atmPut.coinswitchIv}%)\n` +
        `   • Delta: $${res.atmPut.deltaPrice.toFixed(2)} (IV ${res.atmPut.deltaIv}%)\n` +
        `   • Diff: $${res.atmPut.diff >= 0 ? "+" : ""}${res.atmPut.diff.toFixed(2)} (${res.atmPut.diffPct >= 0 ? "+" : ""}${res.atmPut.diffPct.toFixed(1)}%)`
      );
    }
    lines.push("");

    if (res.otmCalls.length > 0) {
      lines.push("📈 *OTM CALLS (Strikes > Spot)*");
      lines.push("----------------------------------------");
      for (const c of res.otmCalls) {
        const tag = c.isArbitrage ? "🔥 ARB" : "  ";
        const sprdStr = `${c.diffPct >= 0 ? "+" : ""}${c.diffPct.toFixed(1)}%`;
        lines.push(
          `\`${c.strike.toString().padEnd(6)}\` | CS: $${c.coinswitchPrice.toFixed(1).padStart(6)} | DL: $${c.deltaPrice.toFixed(1).padStart(6)} | Sprd: ${sprdStr.padStart(6)} ${tag}`
        );
      }
      lines.push("");
    }

    if (res.otmPuts.length > 0) {
      lines.push("📉 *OTM PUTS (Strikes < Spot)*");
      lines.push("----------------------------------------");
      for (const p of res.otmPuts) {
        const tag = p.isArbitrage ? "🔥 ARB" : "  ";
        const sprdStr = `${p.diffPct >= 0 ? "+" : ""}${p.diffPct.toFixed(1)}%`;
        lines.push(
          `\`${p.strike.toString().padEnd(6)}\` | CS: $${p.coinswitchPrice.toFixed(1).padStart(6)} | DL: $${p.deltaPrice.toFixed(1).padStart(6)} | Sprd: ${sprdStr.padStart(6)} ${tag}`
        );
      }
      lines.push("");
    }

    lines.push("⏰ *1:30 PM IST DAILY PROCEDURE:*");
    lines.push("1. Verify yesterday's 1:30 PM CoinSwitch trade settlement.");
    lines.push(`2. Re-enter ${lotBtc} BTC next day trade on mispriced strike.`);
    lines.push("3. Type `/balance` to verify equal margin across exchanges.");

    return lines.join("\n");
  }
}
