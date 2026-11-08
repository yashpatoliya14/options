import Decimal from 'decimal.js';

export interface BacktestTrade {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT' | 'buy' | 'sell';
  entryPrice: number;
  exitPrice: number;
  quantityContracts: number;
  notionalUsd: number;
  realizedPnl: number;
  fees: number;
  netPnl: number;
  entryTimestamp: number;
  exitTimestamp: number;
  purpose: string;
}

export interface BacktestReport {
  initialCapitalUsd: number;
  finalEquityUsd: number;
  totalGrossProfitUsd: number;
  totalGrossLossUsd: number;
  netProfitUsd: number;
  returnPercent: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRatePercent: number;
  profitFactor: number;
  maxDrawdownUsd: number;
  maxDrawdownPercent: number;
  sharpeRatio: number;
  totalFeesPaidUsd: number;
  averageTradePnlUsd: number;
  primaryTradesCount: number;
  hedgeTradesCount: number;
}

export class PerformanceCalculator {
  public static calculateReport(
    initialCapital: number,
    trades: BacktestTrade[],
    equityCurve: { timestamp: number; equity: number }[]
  ): BacktestReport {
    let grossProfit = new Decimal(0);
    let grossLoss = new Decimal(0);
    let totalFees = new Decimal(0);
    let winningTrades = 0;
    let losingTrades = 0;
    let primaryTrades = 0;
    let hedgeTrades = 0;

    for (const t of trades) {
      totalFees = totalFees.plus(t.fees);
      if (t.purpose.includes('HEDGE')) hedgeTrades++;
      else primaryTrades++;

      if (t.realizedPnl > 0) {
        grossProfit = grossProfit.plus(t.realizedPnl);
        winningTrades++;
      } else if (t.realizedPnl < 0) {
        grossLoss = grossLoss.plus(Math.abs(t.realizedPnl));
        losingTrades++;
      }
    }

    const netProfit = grossProfit.minus(grossLoss).minus(totalFees);
    const finalEquity = new Decimal(initialCapital).plus(netProfit).toNumber();
    const returnPercent = new Decimal(netProfit).dividedBy(initialCapital).times(100).toNumber();
    const winRatePercent = trades.length > 0 ? (winningTrades / trades.length) * 100 : 0;
    const profitFactor = grossLoss.isZero()
      ? (grossProfit.isZero() ? 1.0 : 999.0)
      : grossProfit.dividedBy(grossLoss).toNumber();

    // Max Drawdown calculation from equity curve
    let peak = initialCapital;
    let maxDdUsd = 0;
    let maxDdPercent = 0;

    for (const pt of equityCurve) {
      if (pt.equity > peak) {
        peak = pt.equity;
      }
      const ddUsd = peak - pt.equity;
      const ddPct = peak > 0 ? (ddUsd / peak) * 100 : 0;

      if (ddUsd > maxDdUsd) maxDdUsd = ddUsd;
      if (ddPct > maxDdPercent) maxDdPercent = ddPct;
    }

    // Sharpe ratio (annualized assuming 4h periods -> 6 periods/day * 365 = 2190 periods/year)
    const returns: number[] = [];
    for (let i = 1; i < equityCurve.length; i++) {
      const prev = equityCurve[i - 1].equity;
      const curr = equityCurve[i].equity;
      if (prev > 0) {
        returns.push((curr - prev) / prev);
      }
    }

    let sharpeRatio = 0;
    if (returns.length > 1) {
      const avgReturn = returns.reduce((a, b) => a + b, 0) / returns.length;
      const variance = returns.reduce((sum, r) => sum + Math.pow(r - avgReturn, 2), 0) / (returns.length - 1);
      const stdDev = Math.sqrt(variance);
      if (stdDev > 0) {
        // Annualize with sqrt(2190)
        sharpeRatio = (avgReturn / stdDev) * Math.sqrt(2190);
      }
    }

    const avgTradePnl = trades.length > 0 ? netProfit.dividedBy(trades.length).toNumber() : 0;

    return {
      initialCapitalUsd: initialCapital,
      finalEquityUsd: Math.round(finalEquity * 100) / 100,
      totalGrossProfitUsd: Math.round(grossProfit.toNumber() * 100) / 100,
      totalGrossLossUsd: Math.round(grossLoss.toNumber() * 100) / 100,
      netProfitUsd: Math.round(netProfit.toNumber() * 100) / 100,
      returnPercent: Math.round(returnPercent * 100) / 100,
      totalTrades: trades.length,
      winningTrades,
      losingTrades,
      winRatePercent: Math.round(winRatePercent * 10) / 10,
      profitFactor: Math.round(profitFactor * 100) / 100,
      maxDrawdownUsd: Math.round(maxDdUsd * 100) / 100,
      maxDrawdownPercent: Math.round(maxDdPercent * 100) / 100,
      sharpeRatio: Math.round(sharpeRatio * 100) / 100,
      totalFeesPaidUsd: Math.round(totalFees.toNumber() * 100) / 100,
      averageTradePnlUsd: Math.round(avgTradePnl * 100) / 100,
      primaryTradesCount: primaryTrades,
      hedgeTradesCount: hedgeTrades,
    };
  }
}

