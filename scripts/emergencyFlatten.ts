/**
 * Emergency Flatten Script
 * Cancels ALL working orders and immediately market closes ALL open positions on Delta Exchange India.
 * Can be run standalone anytime via:
 *   npm run flatten
 *   npm run emergency:flatten
 *   npm run flatten --paper
 */

import { DeltaClient } from '../src/exchange/delta.client';
import { env } from '../src/config/env';
import { telegram } from '../src/monitoring/telegram';
import { getDatabase, closeDatabase } from '../src/database/connection';

async function main() {
  console.log('\n========================================================');
  console.log('       🚨 DELTA EXCHANGE INDIA - EMERGENCY FLATTEN       ');
  console.log('========================================================\n');

  const args = process.argv.slice(2);
  const forcePaper = args.includes('--paper');
  const isLive = !forcePaper && (env.TRADING_MODE === 'LIVE' || args.includes('--live'));

  console.log(`Execution Mode: ${isLive ? '🔴 LIVE (Delta Exchange India)' : '🟡 PAPER / LOCAL SIMULATION'}`);
  console.log(`Timestamp: ${new Date().toISOString()}\n`);

  let cancelledExchangeOrders = 0;
  const closedExchangePositions: { symbol: string; size: number }[] = [];

  // 1. LIVE FLATTEN VIA DELTA EXCHANGE INDIA REST API
  if (isLive) {
    if (!env.DELTA_API_KEY || !env.DELTA_API_SECRET) {
      console.error('❌ Error: DELTA_API_KEY and DELTA_API_SECRET must be configured in .env for LIVE flatten.');
      process.exit(1);
    }

    console.log(`Connecting to Delta Exchange India: ${env.DELTA_BASE_URL}`);
    const deltaClient = new DeltaClient();

    try {
      // Step 1: Cancel all open orders
      console.log('\n[1/3] Cancelling all open / working orders on Delta...');
      const openOrders = await deltaClient.getOpenOrders();
      console.log(`Found ${openOrders.length} working orders on Delta.`);

      for (const order of openOrders) {
        console.log(`  Cancelling Order ID: ${order.id} (${order.product_id}) | ${order.side} ${order.size} contracts...`);
        const ok = await deltaClient.cancelOrder(order.product_id, order.id);
        if (ok) cancelledExchangeOrders++;
      }

      // Also call bulk cancel for gold products
      await deltaClient.cancelAllOrders(131253); // XAUTUSD
      await deltaClient.cancelAllOrders(123006); // PAXGUSD
      console.log(`✅ Open orders cancelled: ${cancelledExchangeOrders}`);

      // Step 2: Fetch and flatten all open positions
      console.log('\n[2/3] Fetching open positions from Delta Exchange India (/v2/positions/margined)...');
      const positions = await deltaClient.getOpenPositions();
      const activePositions = positions.filter((p) => {
        const sz = typeof p.size === 'number' ? p.size : parseFloat(String(p.size || '0'));
        return Math.abs(sz) > 0;
      });

      if (activePositions.length === 0) {
        console.log('✅ No open positions found on Delta Exchange India (Exposure is already 0).');
      } else {
        console.log(`Found ${activePositions.length} open position(s) to close:`);
        for (const pos of activePositions) {
          const size = typeof pos.size === 'number' ? pos.size : parseFloat(String(pos.size || '0'));
          const symbol = pos.product_symbol || `Product ${pos.product_id}`;
          const closingSide: 'buy' | 'sell' = size > 0 ? 'sell' : 'buy';
          const contracts = Math.abs(size);

          console.log(`  Closing ${symbol}: Current size = ${size} contracts -> Market ${closingSide.toUpperCase()} ${contracts} lots with reduce_only: true...`);
          try {
            await deltaClient.closePositionMarket(pos.product_id, size);
            closedExchangePositions.push({ symbol, size });
            console.log(`  ✅ Successfully submitted market close order for ${symbol}`);
          } catch (err: any) {
            console.error(`  ❌ Failed to close position ${symbol}: ${err.message}`);
          }
        }
      }

      // Step 3: Verify zero open positions remaining
      console.log('\n[3/3] Verifying account exposure...');
      const remainingPositions = await deltaClient.getOpenPositions();
      const nonZero = remainingPositions.filter((p) => {
        const sz = typeof p.size === 'number' ? p.size : parseFloat(String(p.size || '0'));
        return Math.abs(sz) > 0;
      });

      if (nonZero.length === 0) {
        console.log('✅ Confirmed: Net account position exposure is ZERO.');
      } else {
        console.warn(`⚠️ Warning: ${nonZero.length} positions still report non-zero size. Please inspect Delta UI.`);
      }
    } catch (err: any) {
      console.error('❌ Error during Delta API flatten execution:', err.message);
      if (err.message.includes('ip_not_whitelisted')) {
        console.error('👉 TIP: Ensure your machine\'s current public IP is added to the API Key Whitelist in Delta Exchange India settings.');
      }
    }
  }

  // 2. CLEANUP LOCAL SQLITE DATABASE
  console.log('\n[Database] Updating local SQLite database state...');
  try {
    const db = getDatabase();

    // Cancel all open orders in database
    const nowIso = new Date().toISOString();
    const cancelledOrdersInfo = db.prepare(`UPDATE orders SET status = 'CANCELLED', updated_at = ? WHERE status = 'OPEN'`).run(nowIso);
    console.log(`  Cancelled ${cancelledOrdersInfo.changes} local open orders.`);

    // Close open positions in database
    const closedPositionsInfo = db.prepare(`UPDATE positions SET quantity = 0, status = 'CLOSED', updated_at = ? WHERE status = 'OPEN'`).run(nowIso);
    console.log(`  Closed ${closedPositionsInfo.changes} local active positions.`);

    // Close open hedges in hedge_entries
    const closedHedgesInfo = db.prepare(`UPDATE hedge_entries SET status = 'CLOSED', closed_at = ? WHERE status = 'OPEN'`).run(nowIso);
    console.log(`  Closed ${closedHedgesInfo.changes} local active hedges.`);

    // Set strategy status to KILLED
    db.prepare(`UPDATE strategies SET status = 'KILLED', updated_at = ?`).run(nowIso);
    db.prepare(`
      UPDATE strategy_states
      SET primary_quantity = 0, primary_average_price = 0, current_grid_level = 0, last_hedge_trigger_level = 0, updated_at = ?
    `).run(nowIso);

    // Record emergency risk event
    db.prepare(`
      INSERT INTO risk_events (id, strategy_id, event_type, description, severity, created_at)
      VALUES (?, ?, 'EMERGENCY_FLATTEN', 'Manual Emergency Flatten Triggered: Closed all positions and cancelled all orders', 'CRITICAL', ?)
    `).run('flatten_' + Date.now(), 'gold-trend-grid-hedge-01', nowIso);

    closeDatabase();
    console.log('✅ SQLite database updated: Strategy set to KILLED.');
  } catch (err: any) {
    console.warn(`  Note: Local SQLite update skipped or error: ${err.message}`);
  }

  // 3. SEND TELEGRAM ALERT IF ENABLED
  if (telegram.isEnabled()) {
    console.log('\n[Telegram] Sending Emergency Flatten alert to Telegram...');
    await telegram.notifyEmergencyFlatten({
      reason: 'Manual Emergency Flatten Triggered via CLI / API',
      status: 'KILLED',
      cancelledOrdersCount: cancelledExchangeOrders,
      closedPositions: closedExchangePositions,
    });
    console.log('✅ Telegram alert dispatched.');
  } else {
    console.log('\n[Telegram] Telegram notifications not enabled (Set TELEGRAM_ENABLED=true in .env to enable).');
  }

  console.log('\n========================================================');
  console.log('           🚨 EMERGENCY FLATTEN COMPLETE 🚨             ');
  console.log('========================================================\n');
  process.exit(0);
}

main().catch((err) => {
  console.error('Fatal emergency flatten error:', err);
  process.exit(1);
});
