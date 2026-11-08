import { EventEmitter } from 'events';
import { DeltaClient } from '../exchange/delta.client';
import { DeltaWebSocketClient } from '../exchange/delta.websocket';
import { StrategyRepository } from '../database/repositories/strategy.repository';
import { OrderRepository } from '../database/repositories/order.repository';
import { PositionRepository } from '../database/repositories/position.repository';
import { HedgeRepository } from '../database/repositories/hedge.repository';
import { TradeRepository } from '../database/repositories/trade.repository';
import { GridRepository } from '../database/repositories/grid.repository';
import { RiskRepository } from '../database/repositories/risk.repository';
import { TrendService } from './trend.service';
import { GridService } from './grid.service';
import { HedgeService } from './hedge.service';
import { PositionManager, PortfolioSummary } from './position.manager';
import { ReversalService } from './reversal.service';
import { RiskManager } from './risk.manager';
import { OrderExecutor } from '../execution/order.executor';
import { OrderReconciler } from '../execution/order.reconciler';
import { StrategyConfig, RiskLimits, StrategyStatus, TradingMode, TrendDirection } from '../types/strategy.types';
import { Precision } from '../utils/precision';
import { logger } from '../monitoring/logger';
import { alerts } from '../monitoring/alerts';

export class StrategyEngine extends EventEmitter {
  private config: StrategyConfig;
  private riskLimits: RiskLimits;
  private status: StrategyStatus = 'STOPPED';

  // Exchange clients
  private deltaClient: DeltaClient;
  private wsClient: DeltaWebSocketClient;

  // Repositories
  private strategyRepo: StrategyRepository;
  private orderRepo: OrderRepository;
  private positionRepo: PositionRepository;
  private hedgeRepo: HedgeRepository;
  private tradeRepo: TradeRepository;
  private gridRepo: GridRepository;
  private riskRepo: RiskRepository;

  // Sub-services
  private trendService: TrendService;
  private gridService: GridService;
  private hedgeService: HedgeService;
  private positionManager: PositionManager;
  private reversalService: ReversalService;
  private riskManager: RiskManager;
  private executor: OrderExecutor;
  private reconciler: OrderReconciler;

  // Live price cache
  private primaryPrice: number = 0;
  private hedgePrice: number = 0;
  private primaryProductId: number = 131253; // XAUTUSD
  private hedgeProductId: number = 123006;   // PAXGUSD
  private contractValue: number = 0.001;

  // Timers
  private candleIntervalTimer?: NodeJS.Timeout;
  private tickTimer?: NodeJS.Timeout;
  private reconcileTimer?: NodeJS.Timeout;

  constructor(
    config: StrategyConfig,
    riskLimits: RiskLimits,
    deltaClient: DeltaClient,
    wsClient: DeltaWebSocketClient
  ) {
    super();
    this.config = config;
    this.riskLimits = riskLimits;
    this.deltaClient = deltaClient;
    this.wsClient = wsClient;

    // Initialize repositories
    this.strategyRepo = new StrategyRepository();
    this.orderRepo = new OrderRepository();
    this.positionRepo = new PositionRepository();
    this.hedgeRepo = new HedgeRepository();
    this.tradeRepo = new TradeRepository();
    this.gridRepo = new GridRepository();
    this.riskRepo = new RiskRepository();

    // Initialize execution layer
    this.executor = new OrderExecutor(this.deltaClient, this.orderRepo, this.tradeRepo, this.config.mode);
    this.reconciler = new OrderReconciler(this.deltaClient, this.orderRepo, this.positionRepo, this.config.id);

    // Initialize position manager
    this.positionManager = new PositionManager(
      this.positionRepo,
      this.hedgeRepo,
      this.strategyRepo,
      this.config.id,
      this.config.primaryInstrument,
      this.primaryProductId,
      this.config.hedgeInstrument,
      this.hedgeProductId,
      this.contractValue,
      this.config.leverage
    );

    // Initialize strategy services
    this.trendService = new TrendService(
      this.deltaClient,
      this.strategyRepo,
      this.config.id,
      this.config.primaryInstrument,
      this.config.timeframe,
      this.config.atrPeriod,
      this.config.atrMultiplier
    );

    this.gridService = new GridService(
      this.executor,
      this.gridRepo,
      this.positionManager,
      this.config.id,
      this.primaryProductId,
      this.config.primaryInstrument,
      this.config.gridSpacingPercent,
      this.config.gridLevels,
      this.config.gridLotsPerLevel,
      this.contractValue
    );

    this.hedgeService = new HedgeService(
      this.executor,
      this.hedgeRepo,
      this.tradeRepo,
      this.strategyRepo,
      this.config.id,
      this.hedgeProductId,
      this.config.hedgeInstrument,
      this.config.hedgeTriggerPercent,
      this.config.hedgeTakeProfitPercent,
      this.config.hedgeLots,
      this.riskLimits.maxOpenHedges,
      this.contractValue
    );

    this.riskManager = new RiskManager(
      this.riskLimits,
      this.tradeRepo,
      this.riskRepo,
      this.config.id
    );

    this.reversalService = new ReversalService(
      this.positionManager,
      this.gridService,
      this.hedgeService,
      this.executor,
      this.strategyRepo,
      this.config.id,
      this.primaryProductId,
      this.config.primaryInstrument,
      this.hedgeProductId,
      this.config.hedgeInstrument
    );

    this.setupWebSocketListeners();
  }

  public getStatus(): StrategyStatus {
    return this.status;
  }

  public getTradingMode(): TradingMode {
    return this.config.mode;
  }

  public setTradingMode(mode: TradingMode): void {
    this.config.mode = mode;
    this.executor.setMode(mode);
    this.strategyRepo.updateStrategy(this.config.id, { mode });
  }

  /**
   * Start the strategy engine
   */
  public async start(): Promise<void> {
    if (this.status === 'RUNNING') return;

    logger.info({ mode: this.config.mode, strategyId: this.config.id }, 'Starting Strategy Engine...');

    // 1. Connect WebSocket
    try {
      this.wsClient.connect();
    } catch (err: any) {
      logger.error({ err: err.message }, 'Failed to start WebSocket');
    }

    // 2. Fetch initial ticker prices via REST to seed cache
    await this.refreshTickerPrices();

    // 3. Evaluate completed candle trend
    await this.processCandleEvaluation();

    // 4. Start background loops
    this.startBackgroundLoops();

    this.status = 'RUNNING';
    this.strategyRepo.updateStrategy(this.config.id, { status: 'RUNNING' });
    alerts.info('Strategy Started', `Engine running in ${this.config.mode} mode on ${this.config.primaryInstrument} & ${this.config.hedgeInstrument}`);
    this.emit('started');
  }

  /**
   * Pause order placement and active triggers
   */
  public pause(): void {
    this.status = 'PAUSED';
    this.strategyRepo.updateStrategy(this.config.id, { status: 'PAUSED' });
    alerts.info('Strategy Paused', 'Trading operations temporarily paused.');
    this.emit('paused');
  }

  /**
   * Resume strategy from paused state
   */
  public resume(): void {
    if (this.status !== 'PAUSED') return;
    this.status = 'RUNNING';
    this.strategyRepo.updateStrategy(this.config.id, { status: 'RUNNING' });
    alerts.info('Strategy Resumed', 'Trading operations resumed.');
    this.emit('resumed');
  }

  /**
   * Stop strategy gracefully
   */
  public async stop(): Promise<void> {
    this.status = 'STOPPED';
    this.stopBackgroundLoops();
    await this.gridService.cancelGridOrders();
    this.strategyRepo.updateStrategy(this.config.id, { status: 'STOPPED' });
    alerts.info('Strategy Stopped', 'Pending orders cancelled.');
    this.emit('stopped');
  }

  /**
   * Kill strategy immediately (emergency)
   */
  public async kill(): Promise<void> {
    this.status = 'KILLED';
    this.riskManager.triggerEmergencyStop('Manual Kill Triggered');
    this.stopBackgroundLoops();
    await this.gridService.cancelGridOrders();
    this.strategyRepo.updateStrategy(this.config.id, { status: 'KILLED' });
    alerts.critical('STRATEGY KILLED', 'Emergency kill executed. All pending orders cancelled.');
    this.emit('killed');
  }

  /**
   * Core trend check and reversal processor
   */
  private async processCandleEvaluation(): Promise<void> {
    if (this.status !== 'RUNNING' && this.status !== 'STOPPED') return;

    try {
      const result = await this.trendService.checkTrend();
      if (!result.hasNewSignal) return;

      const currentState = this.strategyRepo.getState(this.config.id);
      const isReversal = result.isReversal;

      if (isReversal) {
        logger.info(
          { from: result.previousDirection, to: result.direction },
          'Trend Reversal Signal detected'
        );
        await this.reversalService.handleTrendReversal(
          result.direction,
          result.previousDirection,
          this.primaryPrice,
          this.hedgePrice
        );
      }

      // Enter new position cycle if neutral or after reversal
      if (result.direction !== 'NEUTRAL') {
        await this.enterInitialPositionAndGrids(result.direction);
      }
    } catch (err: any) {
      logger.error({ err: err.message }, 'Error during candle evaluation');
    }
  }

  /**
   * Enters 50% initial position and places 5 adverse grid orders
   */
  private async enterInitialPositionAndGrids(direction: TrendDirection): Promise<void> {
    const state = this.strategyRepo.getState(this.config.id);
    if (state && state.primaryQuantity > 0) {
      // Position already active in this cycle
      return;
    }

    if (this.primaryPrice <= 0) {
      await this.refreshTickerPrices();
    }
    const refPrice = this.primaryPrice;
    if (refPrice <= 0) {
      logger.warn('Cannot enter position: Primary price is 0');
      return;
    }

    const side: 'buy' | 'sell' = direction === 'BULLISH' ? 'buy' : 'sell';
    const positionSide = direction === 'BULLISH' ? 'LONG' : 'SHORT';

    // Lot-based initial entry (e.g. 50 lots)
    const initialContracts = this.config.primaryInitialLots;
    const initialNotional = Precision.calculateNotional(initialContracts, refPrice, this.contractValue);

    // Pre-trade risk validation based on lots
    const currentPrimaryLots = state?.primaryQuantity || 0;
    const riskCheck = this.riskManager.validatePreTrade('PRIMARY', initialContracts, currentPrimaryLots, 0);
    if (!riskCheck.allowed) {
      logger.warn({ reason: riskCheck.reason }, 'Initial entry prevented by risk manager');
      return;
    }

    // Place initial 50% market order
    logger.info(
      {
        direction,
        side,
        contracts: initialContracts,
        notional: initialNotional,
        refPrice,
      },
      'Placing 50% Initial Position Entry'
    );

    const initialOrder = await this.executor.placeOrder({
      strategyId: this.config.id,
      productId: this.primaryProductId,
      symbol: this.config.primaryInstrument,
      side,
      orderType: 'market_order',
      quantityContracts: initialContracts,
      purpose: 'PRIMARY_INITIAL',
      contractValue: this.contractValue,
      currentMarketPrice: refPrice,
    });

    const fillPrice = initialOrder.averageFillPrice || refPrice;

    // Update primary position
    this.positionManager.updatePrimaryPositionOnFill(positionSide, initialContracts, fillPrice, refPrice);

    // Update state reference price
    this.strategyRepo.updateState(this.config.id, {
      currentDirection: direction,
      referencePrice: fillPrice,
      primaryAveragePrice: fillPrice,
      primaryQuantity: initialContracts,
      primaryNotional: Precision.calculateNotional(initialContracts, fillPrice, this.contractValue),
    });

    // Place 5 grid limit orders
    await this.gridService.setupGridLevels(direction, fillPrice);

    alerts.info(
      'New Trend Cycle Initiated',
      `Direction: ${direction} | Entry: ${initialContracts} contracts @ ${fillPrice} | Grids: 5 orders placed`
    );
  }

  /**
   * Process price tick updates (hedge triggers, take-profits, paper fills)
   */
  private async processPriceTick(): Promise<void> {
    if (this.status !== 'RUNNING') return;
    if (this.primaryPrice <= 0 || this.hedgePrice <= 0) return;

    try {
      // 1. Simulate paper limit order fills
      if (this.config.mode === 'PAPER' || this.config.mode === 'BACKTEST') {
        const filled = this.executor.checkPaperLimitOrderFills(
          this.config.id,
          this.primaryPrice,
          this.hedgePrice,
          this.contractValue
        );

        for (const f of filled) {
          if (f.purpose === 'GRID_ENTRY') {
            this.gridService.handleGridOrderFilled(f, this.primaryPrice);
          }
        }
      }

      // 2. Evaluate Hedge Take-Profits (1% profit target)
      await this.hedgeService.checkHedgeTakeProfits(this.hedgePrice);

      // 3. Evaluate Hedge Triggers (every 1% favorable movement on primary)
      const state = this.strategyRepo.getState(this.config.id);
      if (state && state.referencePrice > 0 && state.currentDirection !== 'NEUTRAL') {
        const portfolio = this.positionManager.getPortfolioSummary(this.primaryPrice, this.hedgePrice);

        const preCheck = this.riskManager.validatePreTrade(
          'HEDGE',
          this.config.hedgeLots,
          portfolio.primaryPosition?.quantityContracts || 0,
          portfolio.totalHedgeContracts
        );

        if (preCheck.allowed) {
          await this.hedgeService.evaluateHedgeTriggers(
            state.currentDirection,
            state.referencePrice,
            this.primaryPrice,
            this.hedgePrice
          );
        }
      }

      // 4. Update Position Mark Prices & PnL
      this.positionManager.updateMarkPrices(this.primaryPrice, this.hedgePrice);

      // 5. Evaluate Portfolio Risk Limits
      const summary = this.positionManager.getPortfolioSummary(this.primaryPrice, this.hedgePrice);
      this.riskManager.evaluatePortfolioRisk(summary);

      // Emit tick update
      this.emit('tick', {
        primaryPrice: this.primaryPrice,
        hedgePrice: this.hedgePrice,
        summary,
      });
    } catch (err: any) {
      logger.error({ err: err.message }, 'Error processing price tick');
    }
  }

  /**
   * Refresh ticker prices from REST API
   */
  private async refreshTickerPrices(): Promise<void> {
    try {
      const tickers = await this.deltaClient.getTickers();
      const pTicker = tickers.find((t: any) => t.symbol === this.config.primaryInstrument);
      const hTicker = tickers.find((t: any) => t.symbol === this.config.hedgeInstrument);

      if (pTicker && pTicker.mark_price) {
        this.primaryPrice = parseFloat(pTicker.mark_price);
      }
      if (hTicker && hTicker.mark_price) {
        this.hedgePrice = parseFloat(hTicker.mark_price);
      }
    } catch (err: any) {
      logger.error({ err: err.message }, 'Failed to refresh tickers from REST');
    }
  }

  /**
   * Setup Delta WebSocket listeners
   */
  private setupWebSocketListeners(): void {
    this.wsClient.on('ticker', (data: any) => {
      if (data.symbol === this.config.primaryInstrument && data.mark_price) {
        this.primaryPrice = parseFloat(data.mark_price);
      } else if (data.symbol === this.config.hedgeInstrument && data.mark_price) {
        this.hedgePrice = parseFloat(data.mark_price);
      }
    });

    this.wsClient.on('error', (err: any) => {
      logger.error({ err: err.message }, 'WebSocket client error');
    });
  }

  /**
   * Start recurring background timers
   */
  private startBackgroundLoops(): void {
    // 1. Tick processor (every 2 seconds)
    this.tickTimer = setInterval(() => {
      this.processPriceTick().catch((err) => logger.error({ err }, 'Tick loop error'));
    }, 2000);

    // 2. Candle evaluator (every 60 seconds)
    this.candleIntervalTimer = setInterval(() => {
      this.processCandleEvaluation().catch((err) => logger.error({ err }, 'Candle loop error'));
    }, 60000);

    // 3. REST Reconciler (every 30 seconds if live)
    if (this.config.mode === 'LIVE') {
      this.reconcileTimer = setInterval(() => {
        this.reconciler.reconcileOrders().catch((err) => logger.error({ err }, 'Reconcile loop error'));
        this.reconciler.reconcilePositions().catch((err) => logger.error({ err }, 'Position reconcile error'));
      }, 30000);
    }
  }

  /**
   * Stop background timers
   */
  private stopBackgroundLoops(): void {
    if (this.tickTimer) clearInterval(this.tickTimer);
    if (this.candleIntervalTimer) clearInterval(this.candleIntervalTimer);
    if (this.reconcileTimer) clearInterval(this.reconcileTimer);
  }

  /**
   * Get full state snapshot for API / dashboard
   */
  public getSnapshot(): any {
    const summary = this.positionManager.getPortfolioSummary(this.primaryPrice, this.hedgePrice);
    const state = this.strategyRepo.getState(this.config.id);
    const grids = this.gridService.getGridLevels();
    const hedges = this.hedgeService.getOpenHedges();
    const trades = this.tradeRepo.getTrades(this.config.id, 20);
    const riskEvents = this.riskRepo.getRiskEvents(this.config.id, 10);
    const perf = this.tradeRepo.getTotalPerformance(this.config.id);

    return {
      status: this.status,
      mode: this.config.mode,
      direction: state?.currentDirection || 'NEUTRAL',
      referencePrice: state?.referencePrice || 0,
      primaryInstrument: {
        symbol: this.config.primaryInstrument,
        price: this.primaryPrice,
      },
      hedgeInstrument: {
        symbol: this.config.hedgeInstrument,
        price: this.hedgePrice,
      },
      portfolio: summary,
      performance: perf,
      gridLevels: grids,
      openHedges: hedges,
      recentTrades: trades,
      riskEvents,
      emergencyStopped: this.riskManager.isEmergencyStopped(),
      emergencyReason: this.riskManager.getEmergencyReason(),
      timestamp: Date.now(),
    };
  }
}

