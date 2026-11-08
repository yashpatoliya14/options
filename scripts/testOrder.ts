/**
 * Test Order Script for Delta Exchange India
 * Opens a market order for N lots and immediately closes it to test live order execution.
 * 
 * Usage Examples:
 *   npm run test:order --10
 *   npm run test:order -- 10
 *   npm run test:order --lots=10
 *   npm run test:order --lots 10 --symbol=PAXGUSD
 */

import { DeltaClient } from '../src/exchange/delta.client';
import { env } from '../src/config/env';

interface CliArgs {
  lots: number;
  symbol: string;
  side: 'buy' | 'sell';
  isPaper: boolean;
}

function parseCliArgs(): CliArgs {
  const rawArgs = process.argv.slice(2);
  let lots = 10; // Default to 10 lots
  let symbol = env.PRIMARY_INSTRUMENT || 'XAUTUSD';
  let side: 'buy' | 'sell' = 'buy';
  let isPaper = false;

  for (let i = 0; i < rawArgs.length; i++) {
    const arg = rawArgs[i];

    // Handles: --10 or -10
    const dashNumMatch = arg.match(/^-{1,2}(\d+)$/);
    if (dashNumMatch) {
      lots = parseInt(dashNumMatch[1], 10);
      continue;
    }

    // Handles pure numbers: 10
    if (/^\d+$/.test(arg)) {
      lots = parseInt(arg, 10);
      continue;
    }

    // Handles: --lots=10 or -l=10
    const lotsEqMatch = arg.match(/^--(?:lots|size|qty)=(\d+)$/i);
    if (lotsEqMatch) {
      lots = parseInt(lotsEqMatch[1], 10);
      continue;
    }

    // Handles: --lots 10 or -l 10
    if ((arg === '--lots' || arg === '-l' || arg === '--size' || arg === '--qty') && i + 1 < rawArgs.length) {
      const nextArg = rawArgs[i + 1];
      if (/^\d+$/.test(nextArg)) {
        lots = parseInt(nextArg, 10);
        i++;
        continue;
      }
    }

    // Handles: --symbol=PAXGUSD or --hedge
    if (arg === '--hedge') {
      symbol = env.HEDGE_INSTRUMENT || 'PAXGUSD';
      continue;
    }
    const symbolMatch = arg.match(/^--symbol=(.+)$/i);
    if (symbolMatch) {
      symbol = symbolMatch[1].toUpperCase();
      continue;
    }

    // Handles: --side=sell
    const sideMatch = arg.match(/^--side=(buy|sell)$/i);
    if (sideMatch) {
      side = sideMatch[1].toLowerCase() as 'buy' | 'sell';
      continue;
    }

    // Handles: --paper
    if (arg === '--paper') {
      isPaper = true;
      continue;
    }
  }

  return { lots, symbol, side, isPaper };
}

async function runTestOrder() {
  const { lots, symbol, side, isPaper } = parseCliArgs();
  const closeSide: 'buy' | 'sell' = side === 'buy' ? 'sell' : 'buy';

  console.log('===============================================================');
  console.log('  DELTA EXCHANGE INDIA - TEST ORDER EXECUTION (LOT-BASED)       ');
  console.log('===============================================================');
  console.log(` Target Symbol:   ${symbol}`);
  console.log(` Target Size:     ${lots} lots (contracts)`);
  console.log(` Open Direction:  ${side.toUpperCase()}`);
  console.log(` Close Direction: ${closeSide.toUpperCase()}`);
  console.log(` Mode:            ${isPaper ? 'PAPER (Simulation)' : 'LIVE (Delta Exchange India)'}`);
  console.log(` API Base URL:    ${env.DELTA_BASE_URL}`);
  console.log('---------------------------------------------------------------\n');

  if (!isPaper && (!env.DELTA_API_KEY || !env.DELTA_API_SECRET)) {
    console.error('❌ Error: DELTA_API_KEY and DELTA_API_SECRET must be configured in .env for live test orders.');
    console.error('   Or pass --paper to test the execution flow in simulation mode.');
    process.exit(1);
  }

  const client = new DeltaClient();

  // 1. Resolve Product ID & sync server time
  console.log(`[1/4] Connecting to Delta Exchange India & verifying product ${symbol}...`);
  let productId = symbol === 'PAXGUSD' ? 123006 : 131253;
  let markPrice = 0;

  try {
    const products = await client.getProducts();
    const product = products.find((p) => p.symbol === symbol);
    if (product) {
      productId = product.id;
      console.log(`  ✅ Product verified: ID ${productId} (${product.symbol}), Contract Value: ${product.contract_value}`);
    } else {
      console.warn(`  ⚠️ Product ${symbol} not in list, using default ID ${productId}`);
    }

    const tickers = await client.getTickers();
    const ticker = tickers.find((t) => t.product_id === productId || t.symbol === symbol);
    if (ticker && ticker.mark_price) {
      markPrice = parseFloat(ticker.mark_price);
      console.log(`  Live Mark Price: $${markPrice.toFixed(2)}`);
      const notionalEstimate = lots * 0.001 * markPrice;
      console.log(`  Estimated Notional Value for ${lots} lots: ~$${notionalEstimate.toFixed(2)} USD\n`);
    }
  } catch (err: any) {
    console.warn(`  ⚠️ Could not fetch market details (${err.message}). Proceeding with ID ${productId}`);
  }

  if (isPaper) {
    console.log(`[PAPER MODE] Simulating open order of ${lots} lots @ $${markPrice}...`);
    console.log(`  ✅ PAPER OPEN order simulated successfully (ID: paper-test-open-${Date.now()})`);
    console.log(`[PAPER MODE] Waiting 1 second before closing position...`);
    await new Promise((r) => setTimeout(r, 1000));
    console.log(`[PAPER MODE] Simulating close order of ${lots} lots @ $${markPrice}...`);
    console.log(`  ✅ PAPER CLOSE order simulated successfully (ID: paper-test-close-${Date.now()})`);
    console.log('\n===============================================================');
    console.log(`  🎉 SUCCESS! Position is completely FLAT (0 open contracts).`);
    console.log('===============================================================\n');
    return;
  }

  // 2. STEP 1: Place OPEN Order
  console.log(`[2/4] Placing OPEN market order for ${lots} lots of ${symbol} (${side.toUpperCase()})...`);
  try {
    const openOrderResponse = await client.placeOrder({
      product_id: productId,
      size: lots,
      side,
      order_type: 'market_order',
      client_order_id: `test-open-${Date.now()}`,
    });

    console.log(`  ✅ OPEN Order Submitted Successfully!`);
    console.log(`     - Order ID:    ${openOrderResponse.id}`);
    console.log(`     - State:       ${openOrderResponse.state}`);
    console.log(`     - Size:        ${openOrderResponse.size} lots`);
    console.log(`     - Fill Price:  ${openOrderResponse.average_fill_price || 'Market Fill'}\n`);
  } catch (err: any) {
    console.error(`\n❌ Failed to place OPEN order on Delta Exchange India:`);
    console.error(`   ${err.message}`);

    if (err.message.includes('ip_not_whitelisted')) {
      console.log('\n---------------------------------------------------------------');
      console.log('💡 TROUBLESHOOTING TIP: IP Whitelist Required');
      console.log('   Delta Exchange requires your current public IP to be whitelisted');
      console.log('   in your API Key settings on Delta Exchange.');
      console.log('   Go to Delta Exchange -> API Keys -> Edit -> Add your current IP');
      console.log('   (or disable IP whitelisting for unrestricted testing).');
      console.log('---------------------------------------------------------------\n');
    }
    process.exit(1);
  }

  // Brief pause to allow settlement on exchange
  console.log('[3/4] Waiting 1 second before closing position...');
  await new Promise((r) => setTimeout(r, 1000));

  // 3. STEP 2: Place IMMEDIATE CLOSE Order (Opposite Market Order)
  console.log(`[4/4] Placing IMMEDIATE CLOSE market order for ${lots} lots of ${symbol} (${closeSide.toUpperCase()})...`);
  try {
    const closeOrderResponse = await client.placeOrder({
      product_id: productId,
      size: lots,
      side: closeSide,
      order_type: 'market_order',
      reduce_only: true,
      client_order_id: `test-close-${Date.now()}`,
    });

    console.log(`  ✅ CLOSE Order Submitted Successfully!`);
    console.log(`     - Order ID:    ${closeOrderResponse.id}`);
    console.log(`     - State:       ${closeOrderResponse.state}`);
    console.log(`     - Size:        ${closeOrderResponse.size} lots`);
    console.log(`     - Fill Price:  ${closeOrderResponse.average_fill_price || 'Market Fill'}\n`);
  } catch (err: any) {
    console.error(`  ❌ Failed to place CLOSE order! ALERT: Check your position on Delta Exchange manually!`, err.message);
    process.exit(1);
  }

  // 4. Final Position Check
  console.log('Checking final position on Delta Exchange to confirm position is flat...');
  try {
    const positions = await client.getOpenPositions();
    const pos = positions.find((p) => p.product_id === productId);
    const size = pos ? pos.size : 0;

    console.log('---------------------------------------------------------------');
    if (size === 0) {
      console.log(`  🎉 SUCCESS! Position is completely FLAT (0 open contracts).`);
    } else {
      console.log(`  ⚠️ Remaining position size on exchange: ${size} contracts.`);
    }
    console.log('===============================================================\n');
  } catch {
    console.log(`  Test cycle finished. Verify your open positions on Delta India.`);
  }
}

runTestOrder().catch((err) => {
  console.error('Fatal test order error:', err);
  process.exit(1);
});
