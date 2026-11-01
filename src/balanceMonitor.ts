/**
 * Fund Balance & Imbalance Monitor (TypeScript / Node.js).
 * Tracks account equity on Delta Exchange and CoinSwitch.
 * Fires rebalance alerts when one account holds significantly more capital than the other.
 */

import { Config } from "./config";
import { DeltaClient } from "./deltaClient";
import { CoinSwitchClient } from "./coinSwitchClient";
import { BalanceResult } from "./types";

export class BalanceMonitor {
  public deltaClient: DeltaClient;
  public coinSwitchClient: CoinSwitchClient;

  constructor(deltaClient?: DeltaClient, coinSwitchClient?: CoinSwitchClient) {
    this.deltaClient = deltaClient || new DeltaClient();
    this.coinSwitchClient = coinSwitchClient || new CoinSwitchClient();
  }

  /**
   * Check balances and compute fund imbalance metrics.
   */
  public async checkBalances(): Promise<BalanceResult> {
    const [deltaBal, csBal] = await Promise.all([
      this.deltaClient.getWalletBalance(),
      this.coinSwitchClient.getWalletBalance()
    ]);

    const dUsd = deltaBal.balanceUsd;
    const csUsd = csBal.balanceUsd;

    const totalUsd = Math.round((dUsd + csUsd) * 100) / 100;
    const totalInr = Math.round(totalUsd * Config.USD_INR_RATE * 100) / 100;

    const dPct = totalUsd > 0 ? Math.round((dUsd / totalUsd) * 1000) / 10 : 50;
    const csPct = totalUsd > 0 ? Math.round((csUsd / totalUsd) * 1000) / 10 : 50;

    const diffUsd = Math.round(Math.abs(dUsd - csUsd) * 100) / 100;
    const diffInr = Math.round(diffUsd * Config.USD_INR_RATE * 100) / 100;

    const maxBal = Math.max(dUsd, csUsd);
    const imbalancePct = maxBal > 0 ? Math.round((diffUsd / maxBal) * 1000) / 10 : 0;

    const isImbalanced = imbalancePct >= Config.FUND_IMBALANCE_THRESHOLD_PCT;

    const transferUsd = Math.round((diffUsd / 2) * 100) / 100;
    const transferInr = Math.round(transferUsd * Config.USD_INR_RATE * 100) / 100;

    const fromExchange = dUsd > csUsd ? "Delta Exchange" : "CoinSwitch";
    const toExchange = dUsd > csUsd ? "CoinSwitch" : "Delta Exchange";

    return {
      deltaUsd: dUsd,
      deltaInr: Math.round(dUsd * Config.USD_INR_RATE * 100) / 100,
      deltaPct: dPct,
      deltaIsSimulated: deltaBal.isSimulated,
      coinswitchUsd: csUsd,
      coinswitchInr: Math.round(csUsd * Config.USD_INR_RATE * 100) / 100,
      coinswitchPct: csPct,
      coinswitchIsSimulated: csBal.isSimulated,
      totalUsd,
      totalInr,
      diffUsd,
      diffInr,
      imbalancePct,
      isImbalanced,
      thresholdPct: Config.FUND_IMBALANCE_THRESHOLD_PCT,
      transferUsd,
      transferInr,
      fromExchange,
      toExchange
    };
  }

  /**
   * Format balance report for Telegram.
   */
  public formatBalanceReport(bal: BalanceResult): string {
    const lines: string[] = [];

    if (bal.isImbalanced) {
      lines.push("⚠️ *FUND IMBALANCE ALERT* ⚠️");
      lines.push("━━━━━━━━━━━━━━━━━━━━━━");
      lines.push(`One exchange has *${bal.imbalancePct}%* more funds than the other!\n`);
    } else {
      lines.push("💼 *EXCHANGE WALLET BALANCES* 💼");
      lines.push("━━━━━━━━━━━━━━━━━━━━━━");
      lines.push("✅ *Balances are well-balanced across exchanges.*\n");
    }

    const dTag = bal.deltaIsSimulated ? " _(Simulated)_" : " _(Live)_";
    const csTag = bal.coinswitchIsSimulated ? " _(Simulated)_" : " _(Live)_";

    lines.push(
      `🏛 *Delta Exchange:*${dTag}\n` +
      `   • *$${bal.deltaUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${bal.deltaInr.toLocaleString("en-IN")}) — *${bal.deltaPct}%*\n`
    );
    lines.push(
      `🏦 *CoinSwitch:*${csTag}\n` +
      `   • *$${bal.coinswitchUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${bal.coinswitchInr.toLocaleString("en-IN")}) — *${bal.coinswitchPct}%*\n`
    );
    lines.push(`💰 *Total Portfolio:* $${bal.totalUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })} (₹${bal.totalInr.toLocaleString("en-IN")})`);
    lines.push(`⚖️ *Balance Difference:* $${bal.diffUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })} (₹${bal.diffInr.toLocaleString("en-IN")})`);

    if (bal.isImbalanced) {
      lines.push("\n🚨 *ACTION REQUIRED (Transfer Funds):*");
      lines.push(
        `Transfer *$${bal.transferUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (approx *₹${bal.transferInr.toLocaleString("en-IN")}*)\n` +
        `From: *${bal.fromExchange}* ➡️ To: *${bal.toExchange}*\n` +
        `_This will equalize both accounts to 50/50 for optimal arbitrage coverage._`
      );
    } else {
      lines.push(`\n👌 No fund transfer needed right now. (Threshold: ${bal.thresholdPct}%)`);
    }

    return lines.join("\n");
  }
}

