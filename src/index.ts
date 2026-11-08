/**
 * Delta Exchange India - Gold Trend + Grid Hedging Bot
 * Main Application Entry Point
 */

import { initializeDatabase } from './database/initialize';
import { closeDatabase } from './database/connection';
import { DeltaClient } from './exchange/delta.client';
import { DeltaWebSocketClient } from './exchange/delta.websocket';
import { ProductsService } from './exchange/products.service';
import { StrategyEngine } from './strategy/strategy.engine';
import { ApiServer } from './api/server';
import { defaultStrategyConfig, defaultRiskLimits } from './config/strategy.config';
import { env } from './config/env';
import { logger } from './monitoring/logger';
import { alerts } from './monitoring/alerts';

async function bootstrap() {
  logger.info('========================================================');
  logger.info('  DELTA EXCHANGE INDIA - GOLD TREND + GRID HEDGING BOT  ');
  logger.info('========================================================');

  // 1. Initialize SQLite Database
  logger.info('Initializing SQLite database with WAL mode...');
  initializeDatabase();

  // 2. Instantiate Delta Exchange REST and WebSocket Clients
  const deltaClient = new DeltaClient();
  const wsClient = new DeltaWebSocketClient();

  // 3. Dynamic Product Discovery & Verification (Section 2)
  const productsService = new ProductsService(deltaClient);
  try {
    await productsService.verifyGoldProducts(
      defaultStrategyConfig.primaryInstrument,
      defaultStrategyConfig.hedgeInstrument
    );
  } catch (err: any) {
    logger.error({ err: err.message }, 'Product verification failed');
    process.exit(1);
  }

  // 4. Instantiate Strategy Engine
  const strategyEngine = new StrategyEngine(
    defaultStrategyConfig,
    defaultRiskLimits,
    deltaClient,
    wsClient
  );

  // 5. Instantiate and Start REST API & WebSocket Dashboard Server
  const port = parseInt(process.env.PORT || '3000', 10);
  const apiServer = new ApiServer(strategyEngine, port);
  await apiServer.start();

  // 6. Start the Strategy Engine (Default: PAPER mode)
  logger.info({ mode: defaultStrategyConfig.mode }, `Starting Strategy Engine in ${defaultStrategyConfig.mode} mode...`);
  await strategyEngine.start();

  logger.info(`Trading bot is fully operational.`);
  logger.info(`Web Dashboard & REST API: http://localhost:${port}`);

  // Graceful Shutdown Handlers
  const shutdown = async (signal: string) => {
    logger.info({ signal }, `Received ${signal}. Performing graceful shutdown...`);
    try {
      await strategyEngine.stop();
      await apiServer.stop();
      wsClient.disconnect();
      closeDatabase();
      logger.info('Shutdown complete. Goodbye.');
      process.exit(0);
    } catch (err: any) {
      logger.error({ err: err.message }, 'Error during shutdown');
      process.exit(1);
    }
  };

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

bootstrap().catch((err) => {
  logger.error({ err: err.message, stack: err.stack }, 'Fatal application bootstrap error');
  alerts.critical('BOOTSTRAP_FAILURE', `Bot crashed on startup: ${err.message}`);
  process.exit(1);
});

