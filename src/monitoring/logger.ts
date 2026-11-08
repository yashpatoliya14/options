/**
 * Pino Structured Logger
 */

import pino from 'pino';

const isProduction = process.env.NODE_ENV === 'production';
const logLevel = process.env.LOG_LEVEL || 'info';

export const logger = pino({
  level: logLevel,
  transport: !isProduction
    ? {
        target: 'pino-pretty',
        options: {
          colorize: true,
          translateTime: 'yyyy-mm-dd HH:MM:ss.l UTC',
          ignore: 'pid,hostname',
        },
      }
    : undefined,
  base: {
    app: 'delta-gold-hedging-bot',
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});

