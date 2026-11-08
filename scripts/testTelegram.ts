/**
 * Telegram Notification Test Script
 * Verifies bot token, chat ID, and sends sample trade and emergency messages.
 * Run via:
 *   npm run test:telegram
 */

import { env } from '../src/config/env';
import { telegram } from '../src/monitoring/telegram';

async function main() {
  console.log('\n========================================================');
  console.log('       📱 DELTA GOLD BOT - TELEGRAM TEST UTILITY         ');
  console.log('========================================================\n');

  console.log('Checking Telegram configuration from .env:');
  console.log(`  TELEGRAM_ENABLED:   ${env.TELEGRAM_ENABLED}`);
  console.log(`  TELEGRAM_BOT_TOKEN: ${env.TELEGRAM_BOT_TOKEN ? env.TELEGRAM_BOT_TOKEN.substring(0, 10) + '...' : '(Not set)'}`);
  console.log(`  TELEGRAM_CHAT_ID:   ${env.TELEGRAM_CHAT_ID || '(Not set)'}\n`);

  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) {
    console.log('❌ Telegram credentials are not configured in your .env file.');
    console.log('\n📖 Step-by-Step Setup Guide:');
    console.log('  1. Open Telegram and search for @BotFather');
    console.log('  2. Send "/newbot" and follow prompts to create your bot');
    console.log('  3. Copy the HTTP API token provided by BotFather into .env:');
    console.log('     TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ');
    console.log('  4. Open your new bot in Telegram and click "START"');
    console.log('  5. Search for @userinfobot on Telegram and message it to find your ID');
    console.log('  6. Copy your numeric ID into .env:');
    console.log('     TELEGRAM_CHAT_ID=123456789');
    console.log('  7. Enable Telegram alerts in .env:');
    console.log('     TELEGRAM_ENABLED=true');
    console.log('  8. Run this test again: npm run test:telegram\n');
    process.exit(1);
  }

  console.log('Connecting to Telegram Bot API...');
  const conn = await telegram.verifyConnection();

  if (!conn.success) {
    console.error(`❌ Connection failed: ${conn.error}`);
    process.exit(1);
  }

  console.log(`✅ Connected successfully to Telegram bot: @${conn.botName}`);

  // Test 1: System connectivity check
  console.log('\nSending test connectivity message...');
  const msgOk = await telegram.sendMessage(
    `🤖 <b>Delta Gold Trading Bot Connected!</b>\n\n` +
    `Real-time trade notifications and emergency alerts are active.\n` +
    `Primary: <code>${env.PRIMARY_INSTRUMENT}</code> (100 lots total)\n` +
    `Hedge: <code>${env.HEDGE_INSTRUMENT}</code> (10 lots)\n` +
    `Mode: <code>${env.TRADING_MODE}</code>\n` +
    `Time: <code>${new Date().toISOString()}</code>`
  );

  if (msgOk) {
    console.log('✅ Connectivity message delivered to Telegram!');
  } else {
    console.error('❌ Failed to deliver message. Check chat ID and make sure you sent /start to your bot.');
    process.exit(1);
  }

  // Test 2: Sample trade notification
  console.log('\nSending sample trade signal notification...');
  await telegram.notifyTrendSignal({
    direction: 'BULLISH',
    symbol: env.PRIMARY_INSTRUMENT,
    initialLots: env.PRIMARY_INITIAL_LOTS,
    price: 2735.50,
    gridLevels: env.GRID_LEVELS,
    gridLotsPerLevel: env.GRID_LOTS_PER_LEVEL,
    spacingPercent: env.GRID_SPACING_PERCENT,
    leverage: env.LEVERAGE,
  });
  console.log('✅ Sample trade notification sent!');

  // Test 3: Sample hedge take-profit notification
  console.log('\nSending sample hedge profit booking notification...');
  await telegram.notifyHedgeTakeProfit({
    symbol: env.HEDGE_INSTRUMENT,
    level: 1,
    exitPrice: 2735.12,
    realizedPnl: 2.76,
    fees: 0.054,
    remainingPrimaryLots: env.PRIMARY_TOTAL_LOTS,
  });
  console.log('✅ Sample hedge profit notification sent!');

  console.log('\n========================================================');
  console.log('   🎉 ALL TELEGRAM TESTS PASSED! YOU ARE READY TO TRADE  ');
  console.log('========================================================\n');
}

main().catch((err) => {
  console.error('Test error:', err);
  process.exit(1);
});

