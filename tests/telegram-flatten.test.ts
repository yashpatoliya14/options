import { describe, it, expect, beforeAll, vi } from 'vitest';
import { TelegramService } from '../src/monitoring/telegram';
import { AlertService } from '../src/monitoring/alerts';
import { StrategyEngine } from '../src/strategy/strategy.engine';
import { DeltaClient } from '../src/exchange/delta.client';
import { DeltaWebSocketClient } from '../src/exchange/delta.websocket';
import { defaultStrategyConfig, defaultRiskLimits } from '../src/config/strategy.config';
import { initializeDatabase } from '../src/database/initialize';

describe('Telegram Service & Emergency Flatten', () => {
  beforeAll(() => {
    initializeDatabase();
  });

  describe('TelegramService', () => {
    it('initializes in disabled state when credentials not configured', () => {
      const tg = new TelegramService('', '', false);
      expect(tg.isEnabled()).toBe(false);
    });

    it('returns false gracefully without throwing when disabled', async () => {
      const tg = new TelegramService('', '', false);
      const sent = await tg.sendMessage('<b>Test</b>');
      expect(sent).toBe(false);
    });

    it('formats and dispatches trade signal without throwing', async () => {
      const tg = new TelegramService('', '', false);
      await expect(
        tg.notifyTrendSignal({
          direction: 'BULLISH',
          symbol: 'XAUTUSD',
          initialLots: 50,
          price: 2735.5,
          gridLevels: 5,
          gridLotsPerLevel: 10,
          spacingPercent: 0.5,
          leverage: 10,
        })
      ).resolves.not.toThrow();
    });

    it('formats and dispatches order filled notification without throwing', async () => {
      const tg = new TelegramService('', '', false);
      await expect(
        tg.notifyOrderFilled({
          symbol: 'XAUTUSD',
          purpose: 'GRID_ENTRY',
          side: 'buy',
          lots: 10,
          price: 2721.8,
          avgPrice: 2728.65,
          totalLots: 60,
          level: 1,
        })
      ).resolves.not.toThrow();
    });

    it('formats and dispatches hedge profit booking notification without throwing', async () => {
      const tg = new TelegramService('', '', false);
      await expect(
        tg.notifyHedgeTakeProfit({
          symbol: 'PAXGUSD',
          level: 1,
          exitPrice: 2735.12,
          realizedPnl: 2.76,
          fees: 0.05,
          remainingPrimaryLots: 100,
        })
      ).resolves.not.toThrow();
    });

    it('formats and dispatches emergency flatten notification without throwing', async () => {
      const tg = new TelegramService('', '', false);
      await expect(
        tg.notifyEmergencyFlatten({
          reason: 'Manual Emergency Flatten Triggered',
          status: 'KILLED',
          cancelledOrdersCount: 5,
          closedPositions: [
            { symbol: 'XAUTUSD', size: 50 },
            { symbol: 'PAXGUSD', size: 10 },
          ],
        })
      ).resolves.not.toThrow();
    });

    it('receives alerts from AlertService without crashing', () => {
      const tg = new TelegramService('', '', false);
      expect(() => {
        AlertService.notify('Test Alert', 'Alert content', 'WARNING');
      }).not.toThrow();
    });
  });

  describe('Emergency Flatten in StrategyEngine', () => {
    it('executes emergency flatten: transitions to KILLED, stops loops, and cancels orders', async () => {
      const deltaClient = new DeltaClient();
      const wsClient = new DeltaWebSocketClient();
      const engine = new StrategyEngine(
        { ...defaultStrategyConfig, mode: 'PAPER' },
        defaultRiskLimits,
        deltaClient,
        wsClient
      );

      const flattenResult = await engine.emergencyFlatten('Vitest Emergency Flatten Test');

      expect(flattenResult.success).toBe(true);
      expect(engine.getStatus()).toBe('KILLED');
      const snap = engine.getSnapshot();
      expect(snap.status).toBe('KILLED');
      expect(snap.emergencyStopped).toBe(true);
      expect(snap.portfolio.totalExposureUsd).toBe(0);
    });
  });
});

