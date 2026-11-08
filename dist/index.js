"use strict";
/**
 * Delta Exchange India - Gold Trend + Grid Hedging Bot
 * Main Application Entry Point
 */
Object.defineProperty(exports, "__esModule", { value: true });
const initialize_1 = require("./database/initialize");
const connection_1 = require("./database/connection");
const delta_client_1 = require("./exchange/delta.client");
const delta_websocket_1 = require("./exchange/delta.websocket");
const products_service_1 = require("./exchange/products.service");
const strategy_engine_1 = require("./strategy/strategy.engine");
const server_1 = require("./api/server");
const strategy_config_1 = require("./config/strategy.config");
const logger_1 = require("./monitoring/logger");
const alerts_1 = require("./monitoring/alerts");
async function bootstrap() {
    logger_1.logger.info('========================================================');
    logger_1.logger.info('  DELTA EXCHANGE INDIA - GOLD TREND + GRID HEDGING BOT  ');
    logger_1.logger.info('========================================================');
    // 1. Initialize SQLite Database
    logger_1.logger.info('Initializing SQLite database with WAL mode...');
    (0, initialize_1.initializeDatabase)();
    // 2. Instantiate Delta Exchange REST and WebSocket Clients
    const deltaClient = new delta_client_1.DeltaClient();
    const wsClient = new delta_websocket_1.DeltaWebSocketClient();
    // 3. Dynamic Product Discovery & Verification (Section 2)
    const productsService = new products_service_1.ProductsService(deltaClient);
    try {
        await productsService.verifyGoldProducts(strategy_config_1.defaultStrategyConfig.primaryInstrument, strategy_config_1.defaultStrategyConfig.hedgeInstrument);
    }
    catch (err) {
        logger_1.logger.error({ err: err.message }, 'Product verification failed');
        process.exit(1);
    }
    // 4. Instantiate Strategy Engine
    const strategyEngine = new strategy_engine_1.StrategyEngine(strategy_config_1.defaultStrategyConfig, strategy_config_1.defaultRiskLimits, deltaClient, wsClient);
    // 5. Instantiate and Start REST API & WebSocket Dashboard Server
    const port = parseInt(process.env.PORT || '3000', 10);
    const apiServer = new server_1.ApiServer(strategyEngine, port);
    await apiServer.start();
    // 6. Start the Strategy Engine (Default: PAPER mode)
    logger_1.logger.info({ mode: strategy_config_1.defaultStrategyConfig.mode }, `Starting Strategy Engine in ${strategy_config_1.defaultStrategyConfig.mode} mode...`);
    await strategyEngine.start();
    logger_1.logger.info(`Trading bot is fully operational.`);
    logger_1.logger.info(`Web Dashboard & REST API: http://localhost:${port}`);
    // Graceful Shutdown Handlers
    const shutdown = async (signal) => {
        logger_1.logger.info({ signal }, `Received ${signal}. Performing graceful shutdown...`);
        try {
            await strategyEngine.stop();
            await apiServer.stop();
            wsClient.disconnect();
            (0, connection_1.closeDatabase)();
            logger_1.logger.info('Shutdown complete. Goodbye.');
            process.exit(0);
        }
        catch (err) {
            logger_1.logger.error({ err: err.message }, 'Error during shutdown');
            process.exit(1);
        }
    };
    process.on('SIGINT', () => shutdown('SIGINT'));
    process.on('SIGTERM', () => shutdown('SIGTERM'));
}
bootstrap().catch((err) => {
    logger_1.logger.error({ err: err.message, stack: err.stack }, 'Fatal application bootstrap error');
    alerts_1.alerts.critical('BOOTSTRAP_FAILURE', `Bot crashed on startup: ${err.message}`);
    process.exit(1);
});
