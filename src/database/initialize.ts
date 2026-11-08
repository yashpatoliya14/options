/**
 * Database Initialization & Migration Runner
 */

import fs from 'fs';
import path from 'path';
import { getDatabase } from './connection';
import { logger } from '../monitoring/logger';
import { defaultStrategyConfig } from '../config/strategy.config';
import { INIT_SCHEMA_SQL } from './schema.sql';

export function initializeDatabase(customDbPath?: string): void {
  const db = getDatabase(customDbPath);

  logger.info('Initializing SQLite schema & migrations...');

  let sql = INIT_SCHEMA_SQL;

  // Attempt to load from disk if migration file exists
  const candidatePaths = [
    path.resolve(__dirname, 'migrations/001_init.sql'),
    path.resolve(process.cwd(), 'src/database/migrations/001_init.sql'),
    path.resolve(__dirname, '../../src/database/migrations/001_init.sql'),
  ];

  for (const candidate of candidatePaths) {
    if (fs.existsSync(candidate)) {
      try {
        sql = fs.readFileSync(candidate, 'utf-8');
        break;
      } catch {
        // Fall back to embedded INIT_SCHEMA_SQL
      }
    }
  }

  db.transaction(() => {
    db.exec(sql);
  })();

  // Seed default strategy if not already created
  const existingStrategy = db.prepare('SELECT id FROM strategies WHERE id = ?').get(defaultStrategyConfig.id);

  if (!existingStrategy) {
    const now = new Date().toISOString();

    const insertStrategy = db.prepare(`
      INSERT INTO strategies (id, name, instrument_primary, instrument_hedge, status, mode, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const insertConfig = db.prepare(`
      INSERT INTO strategy_configs (
        id, strategy_id, timeframe, atr_period, atr_multiplier,
        initial_position_percent, grid_spacing_percent, hedge_trigger_percent,
        hedge_take_profit_percent, max_primary_notional, max_hedge_notional,
        leverage, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const insertState = db.prepare(`
      INSERT INTO strategy_states (
        id, strategy_id, current_direction, last_processed_candle,
        reference_price, primary_average_price, primary_quantity,
        current_grid_level, last_hedge_trigger_level, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    db.transaction(() => {
      insertStrategy.run(
        defaultStrategyConfig.id,
        defaultStrategyConfig.name,
        defaultStrategyConfig.primaryInstrument,
        defaultStrategyConfig.hedgeInstrument,
        'STOPPED',
        defaultStrategyConfig.mode,
        now,
        now
      );

      insertConfig.run(
        `cfg-${defaultStrategyConfig.id}`,
        defaultStrategyConfig.id,
        defaultStrategyConfig.timeframe,
        defaultStrategyConfig.atrPeriod,
        defaultStrategyConfig.atrMultiplier,
        defaultStrategyConfig.primaryInitialLots,
        defaultStrategyConfig.gridSpacingPercent,
        defaultStrategyConfig.hedgeTriggerPercent,
        defaultStrategyConfig.hedgeTakeProfitPercent,
        defaultStrategyConfig.primaryTotalLots,
        defaultStrategyConfig.primaryTotalLots,
        defaultStrategyConfig.leverage,
        now,
        now
      );

      insertState.run(
        `state-${defaultStrategyConfig.id}`,
        defaultStrategyConfig.id,
        'NEUTRAL',
        0,
        0,
        0,
        0,
        0,
        0,
        now
      );
    })();

    logger.info({ strategyId: defaultStrategyConfig.id }, 'Seeded default strategy and config in SQLite');
  } else {
    logger.info({ strategyId: defaultStrategyConfig.id }, 'Default strategy already registered in SQLite');
  }

  logger.info('✅ Database initialization complete.');
}

