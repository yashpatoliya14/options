/**
 * SQLite Database Connection Module using better-sqlite3
 */

import Database, { Database as DatabaseInstance } from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import { env } from '../config/env';
import { logger } from '../monitoring/logger';

let dbInstance: DatabaseInstance | null = null;

export function getDatabase(dbPath?: string): DatabaseInstance {
  if (dbInstance) {
    return dbInstance;
  }

  const resolvedPath = path.resolve(process.cwd(), dbPath || env.DATABASE_PATH);
  const dir = path.dirname(resolvedPath);

  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
    logger.info({ dir }, 'Created database directory');
  }

  logger.info({ path: resolvedPath }, 'Opening SQLite database connection');

  const db = new Database(resolvedPath, {
    timeout: 5000,
    verbose: (msg) => {
      if (process.env.LOG_SQL === 'true') {
        logger.debug({ sql: msg }, 'SQLite Execution');
      }
    },
  });

  // Enable WAL mode & foreign keys for concurrent reads & safe transactions
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.pragma('busy_timeout = 5000');
  db.pragma('synchronous = NORMAL');

  dbInstance = db;
  return dbInstance;
}

/**
 * Execute automatic database backup to backup directory
 */
export async function backupDatabase(targetBackupDir?: string): Promise<string> {
  const db = getDatabase();
  const backupDir = path.resolve(process.cwd(), targetBackupDir || env.DATABASE_BACKUP_DIR);

  if (!fs.existsSync(backupDir)) {
    fs.mkdirSync(backupDir, { recursive: true });
  }

  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backupFile = path.join(backupDir, `trading-bot-backup-${timestamp}.db`);

  await db.backup(backupFile);
  logger.info({ backupFile }, 'Database backup completed successfully');
  return backupFile;
}

export function closeDatabase(): void {
  if (dbInstance) {
    dbInstance.close();
    dbInstance = null;
    logger.info('Closed database connection');
  }
}

