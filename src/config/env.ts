/**
 * Environment Configuration with Zod Validation
 */

import path from 'path';
import dotenv from 'dotenv';
import { z } from 'zod';

dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const envSchema = z.object({
  DELTA_API_KEY: z.string().default(''),
  DELTA_API_SECRET: z.string().default(''),
  DELTA_BASE_URL: z.string().url().default('https://api.india.delta.exchange'),
  DELTA_WS_URL: z.string().default('wss://socket.india.delta.exchange'),
  TRADING_MODE: z.enum(['BACKTEST', 'PAPER', 'LIVE']).default('PAPER'),

  PRIMARY_INSTRUMENT: z.string().default('XAUTUSD'),
  HEDGE_INSTRUMENT: z.string().default('PAXGUSD'),

  TIMEFRAME: z.string().default('4h'),
  SUPERTREND_ATR_PERIOD: z.coerce.number().int().positive().default(10),
  SUPERTREND_MULTIPLIER: z.coerce.number().positive().default(3.0),

  // Lot-based allocation
  PRIMARY_TOTAL_LOTS: z.coerce.number().int().positive().default(100),
  PRIMARY_INITIAL_LOTS: z.coerce.number().int().positive().default(50),
  GRID_LOTS_PER_LEVEL: z.coerce.number().int().positive().default(10),
  GRID_LEVELS: z.coerce.number().int().positive().default(5),
  GRID_SPACING_PERCENT: z.coerce.number().positive().default(0.5),

  HEDGE_LOTS: z.coerce.number().int().positive().default(10),
  HEDGE_TRIGGER_PERCENT: z.coerce.number().positive().default(1.0),
  HEDGE_TAKE_PROFIT_PERCENT: z.coerce.number().positive().default(1.0),

  LEVERAGE: z.coerce.number().int().positive().default(10),

  // Lot-based risk limits
  MAX_PRIMARY_LOTS: z.coerce.number().int().positive().default(200),
  MAX_HEDGE_LOTS: z.coerce.number().int().positive().default(100),
  MAX_TOTAL_LOTS: z.coerce.number().int().positive().default(300),
  MAX_DAILY_LOSS_USD: z.coerce.number().positive().default(150.0),
  MAX_DRAWDOWN_PERCENT: z.coerce.number().positive().default(15.0),
  MAX_GRID_ORDERS: z.coerce.number().int().positive().default(5),
  MAX_OPEN_HEDGES: z.coerce.number().int().positive().default(10),

  DATABASE_PATH: z.string().default('data/trading-bot.db'),
  DATABASE_BACKUP_DIR: z.string().default('data/backups'),
  LOG_LEVEL: z.string().default('info'),
  PORT: z.coerce.number().int().default(3000),
});

const parsed = envSchema.safeParse(process.env);

if (!parsed.success) {
  console.error('❌ Environment validation failed:', JSON.stringify(parsed.error.format(), null, 2));
  process.exit(1);
}

export const env = parsed.data;
