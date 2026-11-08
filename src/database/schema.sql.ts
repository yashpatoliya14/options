/**
 * Embedded SQLite Schema
 * Guarantees zero missing-file errors when compiled to dist/ under Node.js / systemd
 */

export const INIT_SCHEMA_SQL = `
CREATE TABLE IF NOT EXISTS trading_accounts (
  id TEXT PRIMARY KEY,
  exchange TEXT NOT NULL DEFAULT 'DELTA_INDIA',
  account_name TEXT NOT NULL,
  api_key_encrypted TEXT,
  api_secret_encrypted TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  instrument_primary TEXT NOT NULL,
  instrument_hedge TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'STOPPED',
  mode TEXT NOT NULL DEFAULT 'PAPER',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_configs (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  timeframe TEXT NOT NULL DEFAULT '4h',
  atr_period INTEGER NOT NULL DEFAULT 10,
  atr_multiplier REAL NOT NULL DEFAULT 3.0,
  initial_position_percent REAL NOT NULL DEFAULT 50.0,
  grid_spacing_percent REAL NOT NULL DEFAULT 0.5,
  hedge_trigger_percent REAL NOT NULL DEFAULT 1.0,
  hedge_take_profit_percent REAL NOT NULL DEFAULT 1.0,
  max_primary_notional REAL NOT NULL DEFAULT 2000.0,
  max_hedge_notional REAL NOT NULL DEFAULT 1000.0,
  leverage INTEGER NOT NULL DEFAULT 10,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_states (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL UNIQUE REFERENCES strategies(id) ON DELETE CASCADE,
  current_direction TEXT NOT NULL DEFAULT 'NEUTRAL',
  last_processed_candle INTEGER NOT NULL DEFAULT 0,
  reference_price REAL NOT NULL DEFAULT 0,
  primary_average_price REAL NOT NULL DEFAULT 0,
  primary_quantity INTEGER NOT NULL DEFAULT 0,
  current_grid_level INTEGER NOT NULL DEFAULT 0,
  last_hedge_trigger_level INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  timeframe TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  CONSTRAINT uq_product_tf_timestamp UNIQUE (product_id, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(product_id, timeframe, timestamp DESC);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  exchange_order_id TEXT,
  client_order_id TEXT NOT NULL UNIQUE,
  product_id INTEGER NOT NULL,
  side TEXT NOT NULL,
  order_type TEXT NOT NULL,
  price REAL,
  quantity INTEGER NOT NULL,
  filled_quantity INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  purpose TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_id, status);

CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL,
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  average_entry_price REAL NOT NULL,
  unrealized_pnl REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'OPEN',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_lookup ON positions(strategy_id, product_id, status);

CREATE TABLE IF NOT EXISTS grid_levels (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  level INTEGER NOT NULL,
  trigger_price REAL NOT NULL,
  quantity INTEGER NOT NULL,
  order_id TEXT REFERENCES orders(id),
  status TEXT NOT NULL DEFAULT 'PENDING',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_grid_lookup ON grid_levels(strategy_id, level);

CREATE TABLE IF NOT EXISTS hedge_entries (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  trigger_level INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  side TEXT NOT NULL,
  entry_price REAL NOT NULL,
  quantity INTEGER NOT NULL,
  target_price REAL NOT NULL,
  realised_pnl REAL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'PENDING',
  order_id TEXT,
  take_profit_order_id TEXT,
  created_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hedges_lookup ON hedge_entries(strategy_id, trigger_level, status);

CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  order_id TEXT,
  product_id INTEGER NOT NULL,
  side TEXT NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL,
  quantity INTEGER NOT NULL,
  realised_pnl REAL DEFAULT 0,
  fees REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id, created_at DESC);

CREATE TABLE IF NOT EXISTS risk_events (
  id TEXT PRIMARY KEY,
  strategy_id TEXT REFERENCES strategies(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  description TEXT NOT NULL,
  severity TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_performance (
  id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  realised_pnl REAL NOT NULL DEFAULT 0,
  unrealised_pnl REAL NOT NULL DEFAULT 0,
  fees REAL NOT NULL DEFAULT 0,
  funding_cost REAL NOT NULL DEFAULT 0,
  total_trades INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  CONSTRAINT uq_strategy_date UNIQUE (strategy_id, date)
);

CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id TEXT,
  action TEXT NOT NULL,
  request_data TEXT,
  response_data TEXT,
  success INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
`;
