/**
 * Fund Balance & Imbalance Monitor (TypeScript / Node.js).
 * LIVE ONLY:
 * - Alerts if balance is 0 on either exchange
 * - Checks if available margin is sufficient for TRADE_LOT_BTC
 * - Rebalance alerts when capital is uneven
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

  public async checkBalances(): Promise<BalanceResult> {
    const [deltaBal, csBal, spotPrice] = await Promise.all([
      this.deltaClient.getWalletBalance(),
      this.coinSwitchClient.getWalletBalance(),
      this.deltaClient.getSpotPrice()
    ]);

    const dUsd = deltaBal.balanceUsd;
    const csUsd = csBal.balanceUsd;

    const totalUsd = Math.round((dUsd + csUsd) * 100) / 100;
    const totalInr = Math.round(totalUsd * Config.USD_INR_RATE * 100) / 100;

    const dPct = totalUsd > 0 ? Math.round((dUsd / totalUsd) * 1000) / 10 : (dUsd > 0 ? 100 : 0);
    const csPct = totalUsd > 0 ? Math.round((csUsd / totalUsd) * 1000) / 10 : (csUsd > 0 ? 100 : 0);

    const diffUsd = Math.round(Math.abs(dUsd - csUsd) * 100) / 100;
    const diffInr = Math.round(diffUsd * Config.USD_INR_RATE * 100) / 100;

    const maxBal = Math.max(dUsd, csUsd);
    const imbalancePct = maxBal > 0 ? Math.round((diffUsd / maxBal) * 1000) / 10 : 0;

    const isImbalanced = imbalancePct >= Config.FUND_IMBALANCE_THRESHOLD_PCT;

    // Check for 0 balance
    const zeroBalanceExchanges: string[] = [];
    if (dUsd <= 0) zeroBalanceExchanges.push("Delta Exchange");
    if (csUsd <= 0) zeroBalanceExchanges.push("CoinSwitch");
    const hasZeroBalance = zeroBalanceExchanges.length > 0;

    // Transfer suggestion: half difference from higher to lower
    const transferUsd = Math.round((diffUsd / 2) * 100) / 100;
    const transferInr = Math.round(transferUsd * Config.USD_INR_RATE * 100) / 100;

    const fromExchange = dUsd > csUsd ? "Delta Exchange" : "CoinSwitch";
    const toExchange = dUsd > csUsd ? "CoinSwitch" : "Delta Exchange";

    // Margin check for TRADE_LOT_BTC
    const tradeLotBtc = Config.TRADE_LOT_BTC;
    const deltaContracts = Math.round(tradeLotBtc / 0.001);
    // ~12% initial margin needed per exchange for short position
    const estimatedMarginPerExchangeUsd = Math.round(tradeLotBtc * spotPrice * 0.12 * 100) / 100;

    const canExecuteTrade = dUsd >= estimatedMarginPerExchangeUsd && csUsd >= estimatedMarginPerExchangeUsd;

    return {
      deltaUsd: dUsd,
      deltaInr: Math.round(dUsd * Config.USD_INR_RATE * 100) / 100,
      deltaPct: dPct,
      deltaError: deltaBal.error,
      coinswitchUsd: csUsd,
      coinswitchInr: Math.round(csUsd * Config.USD_INR_RATE * 100) / 100,
      coinswitchPct: csPct,
      coinswitchError: csBal.error,
      totalUsd,
      totalInr,
      diffUsd,
      diffInr,
      imbalancePct,
      isImbalanced,
      hasZeroBalance,
      zeroBalanceExchanges,
      thresholdPct: Config.FUND_IMBALANCE_THRESHOLD_PCT,
      transferUsd,
      transferInr,
      fromExchange,
      toExchange,
      tradeLotBtc,
      deltaContracts,
      estimatedMarginPerExchangeUsd,
      canExecuteTrade
    };
  }

  public formatBalanceReport(bal: BalanceResult): string {
    const lines: string[] = [];

    // 1. Zero Balance Alert
    if (bal.hasZeroBalance) {
      lines.push("🚨 *CRITICAL ZERO BALANCE ALERT* 🚨");
      lines.push("━━━━━━━━━━━━━━━━━━━━━━");
      lines.push(`⚠️ Balance is *0* on: *${bal.zeroBalanceExchanges.join(", ")}*!\n`);
      lines.push(`👉 *Deposit or transfer funds immediately to enable arbitrage trading.*\n`);
    } else if (bal.isImbalanced) {
      lines.push("⚠️ *FUND IMBALANCE ALERT* ⚠️");
      lines.push("━━━━━━━━━━━━━━━━━━━━━━");
      lines.push(`One exchange has *${bal.imbalancePct}%* more capital than the other!\n`);
    } else {
      lines.push("💼 *LIVE WALLET BALANCES & MARGIN* 💼");
      lines.push("━━━━━━━━━━━━━━━━━━━━━━");
      lines.push("✅ *Both exchanges have balanced capital.*\n");
    }

    lines.push(
      `🏛 *Delta Exchange (Live):*\n` +
      `   • Balance: *$${bal.deltaUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${bal.deltaInr.toLocaleString("en-IN")}) — *${bal.deltaPct}%*`
    );
    if (bal.deltaError) {
      lines.push(`   • ⚠️ Status: ${bal.deltaError}`);
    }

    lines.push(
      `\n🏦 *CoinSwitch PRO (Live):*\n` +
      `   • Balance: *$${bal.coinswitchUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (₹${bal.coinswitchInr.toLocaleString("en-IN")}) — *${bal.coinswitchPct}%*`
    );
    if (bal.coinswitchError) {
      lines.push(`   • ⚠️ Status: ${bal.coinswitchError}`);
    }

    lines.push(`\n💰 *Total Combined Equity:* $${bal.totalUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })} (₹${bal.totalInr.toLocaleString("en-IN")})`);
    lines.push(`⚖️ *Balance Gap:* $${bal.diffUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}`);

    // Margin & Lot Verification
    lines.push("\n📦 *LOT SIZING & MARGIN REQUIREMENT:*");
    lines.push(`   • Lot Size: *${bal.tradeLotBtc} BTC* (Delta: *${bal.deltaContracts} contracts*)`);
    lines.push(`   • Estimated Margin Needed: ~*$${bal.estimatedMarginPerExchangeUsd.toFixed(0)}* (₹${Math.round(bal.estimatedMarginPerExchangeUsd * Config.USD_INR_RATE).toLocaleString("en-IN")}) per exchange`);

    if (bal.canExecuteTrade) {
      lines.push(`   • Status: 🟢 *SUFFICIENT MARGIN AVAILABLE ON BOTH EXCHANGES*`);
    } else {
      lines.push(`   • Status: 🔴 *INSUFFICIENT MARGIN! Top up accounts before entering trade.*`);
    }

    // Rebalance suggestion
    if (bal.isImbalanced && !bal.hasZeroBalance) {
      lines.push("\n🚨 *RECOMMENDED REBALANCE TRANSFER:*");
      lines.push(
        `Transfer *$${bal.transferUsd.toLocaleString("en-US", { minimumFractionDigits: 2 })}* (approx *₹${bal.transferInr.toLocaleString("en-IN")}*)\n` +
        `From: *${bal.fromExchange}* ➡️ To: *${bal.toExchange}*\n` +
        `_Equalizes both accounts to 50/50 for optimal arbitrage coverage._`
      );
    }

    return lines.join("\n");
  }
}
