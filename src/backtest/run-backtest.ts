import { DeltaClient } from '../exchange/delta.client';
import { BacktestEngine } from './backtest.engine';
import { DeltaCandle } from '../types/exchange.types';

async function main() {
  console.log('=====================================================');
  console.log('  DELTA EXCHANGE INDIA - GOLD TREND + GRID BACKTEST  ');
  console.log('=====================================================\n');

  const deltaClient = new DeltaClient();
  const symbol = 'XAUTUSD';
  const timeframe = '4h';

  console.log(`Fetching 4h historical candles for ${symbol} from Delta Exchange India...`);

  // Fetch 30 days of 4h candles (30 * 6 = 180 candles)
  const nowSec = Math.floor(Date.now() / 1000);
  const startSec = nowSec - 60 * 86400; // 60 days

  let candles: DeltaCandle[] = [];
  try {
    candles = await deltaClient.getCandles(symbol, timeframe, startSec, nowSec);
    console.log(`Successfully fetched ${candles.length} candles from Delta Exchange.\n`);
  } catch (err: any) {
    console.warn(`Could not fetch live candles (${err.message}). Generating sample historical series...`);
    candles = generateSampleCandles(200);
  }

  if (candles.length < 20) {
    console.log('Generating additional sample candles to reach sufficient test data...');
    candles = generateSampleCandles(200);
  }

  const engine = new BacktestEngine({
    initialCapitalUsd: 2000,
    primaryTotalLots: 100,
    primaryInitialLots: 50,
    gridSpacingPercent: 0.5,
    gridLevelsCount: 5,
    gridLotsPerLevel: 10,
    hedgeTriggerPercent: 1.0,
    hedgeTakeProfitPercent: 1.0,
    hedgeLots: 10,
    atrPeriod: 10,
    atrMultiplier: 3.0,
  });

  const { report, trades } = engine.run(candles);

  console.log('----------------- BACKTEST RESULTS ------------------');
  console.log(`Initial Capital:       $${report.initialCapitalUsd.toFixed(2)}`);
  console.log(`Final Equity:          $${report.finalEquityUsd.toFixed(2)}`);
  console.log(`Net Profit / Loss:     $${report.netProfitUsd.toFixed(2)} (${report.returnPercent.toFixed(2)}%)`);
  console.log(`Total Trades Executed: ${report.totalTrades} (Primary: ${report.primaryTradesCount}, Hedges: ${report.hedgeTradesCount})`);
  console.log(`Winning Trades:        ${report.winningTrades} / Losing: ${report.losingTrades}`);
  console.log(`Win Rate:              ${report.winRatePercent}%`);
  console.log(`Profit Factor:         ${report.profitFactor}`);
  console.log(`Max Drawdown:          $${report.maxDrawdownUsd.toFixed(2)} (${report.maxDrawdownPercent.toFixed(2)}%)`);
  console.log(`Sharpe Ratio:          ${report.sharpeRatio}`);
  console.log(`Total Fees Paid:       $${report.totalFeesPaidUsd.toFixed(2)}`);
  console.log(`Avg Trade PnL:         $${report.averageTradePnlUsd.toFixed(2)}`);
  console.log('-----------------------------------------------------\n');

  if (trades.length > 0) {
    console.log('Sample Recent Trades:');
    const recent = trades.slice(-5);
    for (const t of recent) {
      console.log(` - [${t.purpose}] ${t.symbol} ${t.side} qty:${t.quantityContracts} entry:$${t.entryPrice.toFixed(2)} exit:$${t.exitPrice.toFixed(2)} pnl:$${t.realizedPnl.toFixed(2)} net:$${t.netPnl.toFixed(2)}`);
    }
  }
}

function generateSampleCandles(count: number): DeltaCandle[] {
  const candles: DeltaCandle[] = [];
  let price = 2700;
  let time = Math.floor(Date.now() / 1000) - count * 14400;

  for (let i = 0; i < count; i++) {
    const drift = Math.sin(i / 15) * 12 + (Math.random() - 0.48) * 8;
    const open = price;
    const close = price + drift;
    const high = Math.max(open, close) + Math.random() * 5;
    const low = Math.min(open, close) - Math.random() * 5;
    const volume = Math.floor(Math.random() * 1000) + 100;

    candles.push({
      time,
      open: Math.round(open * 100) / 100,
      high: Math.round(high * 100) / 100,
      low: Math.round(low * 100) / 100,
      close: Math.round(close * 100) / 100,
      volume,
    });

    price = close;
    time += 14400;
  }

  return candles;
}

main().catch((err) => {
  console.error('Backtest error:', err);
});

