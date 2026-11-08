import http from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import { StrategyEngine } from '../strategy/strategy.engine';
import { logger } from '../monitoring/logger';
import { alerts } from '../monitoring/alerts';

export class ApiServer {
  private server: http.Server;
  private wss: WebSocketServer;
  private engine: StrategyEngine;
  private port: number;

  constructor(engine: StrategyEngine, port: number = 3000) {
    this.engine = engine;
    this.port = port;

    this.server = http.createServer((req, res) => this.handleHttpRequest(req, res));
    this.wss = new WebSocketServer({ server: this.server, path: '/ws' });

    this.setupWebSocketServer();
    this.setupEngineBroadcasts();
  }

  public start(): Promise<void> {
    return new Promise((resolve) => {
      this.server.listen(this.port, () => {
        logger.info({ port: this.port }, `API & Dashboard Server listening on http://localhost:${this.port}`);
        resolve();
      });
    });
  }

  public stop(): Promise<void> {
    return new Promise((resolve) => {
      this.wss.close(() => {
        this.server.close(() => resolve());
      });
    });
  }

  private handleHttpRequest(req: http.IncomingMessage, res: http.ServerResponse): void {
    const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);
    const pathname = url.pathname;
    const method = req.method?.toUpperCase();

    // CORS headers
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

    if (method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    // Serve HTML Dashboard at root
    if (method === 'GET' && (pathname === '/' || pathname === '/index.html')) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(this.renderDashboardHtml());
      return;
    }

    // Helper to send JSON
    const sendJson = (statusCode: number, data: any) => {
      res.writeHead(statusCode, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(data));
    };

    try {
      // REST API Endpoints
      if (method === 'GET' && pathname === '/api/status') {
        const snap = this.engine.getSnapshot();
        return sendJson(200, {
          status: snap.status,
          mode: snap.mode,
          direction: snap.direction,
          emergencyStopped: snap.emergencyStopped,
          emergencyReason: snap.emergencyReason,
          uptimeSec: Math.floor(process.uptime()),
          timestamp: Date.now(),
        });
      }

      if (method === 'GET' && pathname === '/api/strategy') {
        return sendJson(200, this.engine.getSnapshot());
      }

      if (method === 'GET' && pathname === '/api/positions') {
        const snap = this.engine.getSnapshot();
        return sendJson(200, {
          primaryPosition: snap.portfolio.primaryPosition,
          openHedges: snap.openHedges,
          summary: snap.portfolio,
        });
      }

      if (method === 'GET' && pathname === '/api/orders') {
        const snap = this.engine.getSnapshot();
        return sendJson(200, {
          gridOrders: snap.gridLevels,
          recentOrders: snap.recentTrades,
        });
      }

      if (method === 'GET' && pathname === '/api/trades') {
        const snap = this.engine.getSnapshot();
        return sendJson(200, snap.recentTrades);
      }

      if (method === 'GET' && pathname === '/api/performance') {
        const snap = this.engine.getSnapshot();
        return sendJson(200, {
          performance: snap.performance,
          portfolio: snap.portfolio,
        });
      }

      if (method === 'GET' && pathname === '/api/risk') {
        const snap = this.engine.getSnapshot();
        return sendJson(200, {
          emergencyStopped: snap.emergencyStopped,
          emergencyReason: snap.emergencyReason,
          riskEvents: snap.riskEvents,
          marginUtilizedUsd: snap.portfolio.marginUtilizedUsd,
          totalExposureUsd: snap.portfolio.totalExposureUsd,
        });
      }

      // Control Actions
      if (method === 'POST') {
        if (pathname === '/api/strategy/start') {
          this.engine.start().catch((err) => logger.error({ err }, 'Start failed'));
          return sendJson(200, { success: true, message: 'Strategy starting' });
        }

        if (pathname === '/api/strategy/pause') {
          this.engine.pause();
          return sendJson(200, { success: true, message: 'Strategy paused' });
        }

        if (pathname === '/api/strategy/resume') {
          this.engine.resume();
          return sendJson(200, { success: true, message: 'Strategy resumed' });
        }

        if (pathname === '/api/strategy/stop') {
          this.engine.stop().catch((err) => logger.error({ err }, 'Stop failed'));
          return sendJson(200, { success: true, message: 'Strategy stopping' });
        }

        if (pathname === '/api/strategy/kill') {
          this.engine.kill().catch((err) => logger.error({ err }, 'Kill failed'));
          return sendJson(200, { success: true, message: 'Emergency kill switch executed' });
        }
      }

      // 404 for unknown endpoints
      sendJson(404, { error: 'Not Found', path: pathname });
    } catch (err: any) {
      sendJson(500, { error: err.message || 'Internal Server Error' });
    }
  }

  private setupWebSocketServer(): void {
    this.wss.on('connection', (ws: WebSocket) => {
      // Send initial snapshot on connect
      const snapshot = this.engine.getSnapshot();
      ws.send(JSON.stringify({ type: 'SNAPSHOT', data: snapshot }));

      ws.on('message', (message: string) => {
        try {
          const parsed = JSON.parse(message.toString());
          if (parsed.action === 'PING') {
            ws.send(JSON.stringify({ type: 'PONG', timestamp: Date.now() }));
          }
        } catch {
          // ignore
        }
      });
    });
  }

  private setupEngineBroadcasts(): void {
    // Broadcast tick updates to all connected UI clients
    this.engine.on('tick', (payload: any) => {
      this.broadcast({ type: 'TICK', data: payload });
    });

    this.engine.on('started', () => this.broadcast({ type: 'STATUS', status: 'RUNNING' }));
    this.engine.on('paused', () => this.broadcast({ type: 'STATUS', status: 'PAUSED' }));
    this.engine.on('resumed', () => this.broadcast({ type: 'STATUS', status: 'RUNNING' }));
    this.engine.on('stopped', () => this.broadcast({ type: 'STATUS', status: 'STOPPED' }));
    this.engine.on('killed', () => this.broadcast({ type: 'STATUS', status: 'KILLED' }));

    alerts.onAlert((alert) => {
      this.broadcast({ type: 'ALERT', data: alert });
    });
  }

  private broadcast(message: any): void {
    const payload = JSON.stringify(message);
    for (const client of this.wss.clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(payload);
      }
    }
  }

  /**
   * Generates self-contained, sleek Dark Theme Web Dashboard
   */
  private renderDashboardHtml(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Delta India | Gold Trend + Grid Hedging Bot</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg: #0b0e14;
      --card-bg: #151a24;
      --border: #232b3b;
      --accent-gold: #d4af37;
      --accent-blue: #3b82f6;
      --accent-green: #10b981;
      --accent-red: #ef4444;
      --text-main: #f3f4f6;
      --text-muted: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 20px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 20px;
    }
    .header h1 {
      font-size: 1.5rem;
      color: var(--accent-gold);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .status-badge {
      padding: 6px 14px;
      border-radius: 9999px;
      font-weight: 700;
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .badge-RUNNING { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border: 1px solid var(--accent-green); }
    .badge-PAUSED { background: rgba(245, 158, 11, 0.2); color: #f59e0b; border: 1px solid #f59e0b; }
    .badge-STOPPED { background: rgba(156, 163, 175, 0.2); color: var(--text-muted); border: 1px solid var(--text-muted); }
    .badge-KILLED { background: rgba(239, 68, 68, 0.2); color: var(--accent-red); border: 1px solid var(--accent-red); }

    .grid-container {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .card-title {
      font-size: 0.8rem;
      color: var(--text-muted);
      text-transform: uppercase;
      margin-bottom: 8px;
    }
    .card-value {
      font-size: 1.6rem;
      font-weight: 700;
    }
    .card-sub {
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-top: 4px;
    }
    .val-green { color: var(--accent-green); }
    .val-red { color: var(--accent-red); }
    .val-gold { color: var(--accent-gold); }

    .controls-bar {
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
      flex-wrap: wrap;
    }
    button {
      padding: 10px 20px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: opacity 0.2s;
    }
    button:hover { opacity: 0.85; }
    .btn-start { background: var(--accent-green); color: #fff; }
    .btn-pause { background: #f59e0b; color: #fff; }
    .btn-resume { background: var(--accent-blue); color: #fff; }
    .btn-stop { background: #4b5563; color: #fff; }
    .btn-kill { background: var(--accent-red); color: #fff; }

    .tables-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }
    @media (max-width: 900px) {
      .tables-grid { grid-template-columns: 1fr; }
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 0.85rem;
    }
    th, td {
      padding: 10px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }
    th { color: var(--text-muted); font-weight: 600; }
    .log-box {
      background: #06080c;
      border: 1px solid var(--border);
      border-radius: 6px;
      height: 180px;
      overflow-y: auto;
      padding: 10px;
      font-family: monospace;
      font-size: 0.8rem;
      color: #94a3b8;
      margin-top: 20px;
    }
    .log-entry { margin-bottom: 4px; }
  </style>
</head>
<body>
  <div class="header">
    <h1>
      <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
      </svg>
      Delta India | Gold Trend + Grid Hedging Bot
    </h1>
    <div style="display:flex; align-items:center; gap:12px;">
      <span id="modeBadge" style="font-size:0.8rem; color:var(--accent-gold); font-weight:700;">[PAPER MODE]</span>
      <span id="statusBadge" class="status-badge badge-STOPPED">STOPPED</span>
    </div>
  </div>

  <div class="controls-bar">
    <button class="btn-start" onclick="sendAction('start')">▶ START BOT</button>
    <button class="btn-pause" onclick="sendAction('pause')">⏸ PAUSE</button>
    <button class="btn-resume" onclick="sendAction('resume')">⏯ RESUME</button>
    <button class="btn-stop" onclick="sendAction('stop')">⏹ STOP</button>
    <button class="btn-kill" onclick="sendAction('kill')">⚠ EMERGENCY KILL</button>
  </div>

  <div class="grid-container">
    <div class="card">
      <div class="card-title">4H Supertrend Signal</div>
      <div id="trendDirection" class="card-value val-gold">NEUTRAL</div>
      <div id="refPrice" class="card-sub">Reference Price: $0.00</div>
    </div>
    <div class="card">
      <div class="card-title">Primary Gold (XAUTUSD)</div>
      <div id="primaryPrice" class="card-value">$0.00</div>
      <div id="primaryPosition" class="card-sub">Position: 0 contracts ($0.00 notional)</div>
    </div>
    <div class="card">
      <div class="card-title">Hedge Gold (PAXGUSD)</div>
      <div id="hedgePrice" class="card-value">$0.00</div>
      <div id="hedgeStats" class="card-sub">Active Hedges: 0 / 10</div>
    </div>
    <div class="card">
      <div class="card-title">Combined Portfolio P&L</div>
      <div id="totalPnl" class="card-value val-green">$0.00</div>
      <div id="pnlDetails" class="card-sub">Unrealized: $0.00 | Realized: $0.00</div>
    </div>
  </div>

  <div class="tables-grid">
    <div class="card">
      <h3 style="color:var(--text-main); font-size:1rem;">Active Grid Orders (0.5% Spacing)</h3>
      <table>
        <thead>
          <tr>
            <th>Level</th>
            <th>Trigger Price</th>
            <th>Contracts</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="gridTableBody">
          <tr><td colspan="4" style="color:var(--text-muted); text-align:center;">No active grid orders</td></tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <h3 style="color:var(--text-main); font-size:1rem;">Active Hedges (1% Trigger & 1% TP Target)</h3>
      <table>
        <thead>
          <tr>
            <th>Level</th>
            <th>Side</th>
            <th>Entry Price</th>
            <th>Target Price</th>
            <th>Contracts</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="hedgeTableBody">
          <tr><td colspan="6" style="color:var(--text-muted); text-align:center;">No active hedges</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="log-box" id="logBox">
    <div class="log-entry">[SYSTEM] Dashboard initialized. Connecting to WebSocket...</div>
  </div>

  <script>
    let ws;
    const logBox = document.getElementById('logBox');

    function log(msg) {
      const el = document.createElement('div');
      el.className = 'log-entry';
      el.textContent = \`[\${new Date().toLocaleTimeString()}] \${msg}\`;
      logBox.appendChild(el);
      logBox.scrollTop = logBox.scrollHeight;
    }

    function initWebSocket() {
      const loc = window.location;
      const wsUrl = (loc.protocol === 'https:' ? 'wss://' : 'ws://') + loc.host + '/ws';
      ws = new WebSocket(wsUrl);

      ws.onopen = () => log('Connected to live Bot WebSocket');
      ws.onclose = () => {
        log('WebSocket disconnected. Reconnecting in 3s...');
        setTimeout(initWebSocket, 3000);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'SNAPSHOT') updateUI(msg.data);
          else if (msg.type === 'TICK') updateTick(msg.data);
          else if (msg.type === 'ALERT') log(\`ALERT: [\${msg.data.severity}] \${msg.data.title} - \${msg.data.message}\`);
        } catch (e) {
          console.error(e);
        }
      };
    }

    function updateUI(data) {
      const badge = document.getElementById('statusBadge');
      badge.textContent = data.status;
      badge.className = 'status-badge badge-' + data.status;

      document.getElementById('modeBadge').textContent = '[' + data.mode + ' MODE]';
      document.getElementById('trendDirection').textContent = data.direction;
      document.getElementById('trendDirection').className = 'card-value ' + (data.direction === 'BULLISH' ? 'val-green' : (data.direction === 'BEARISH' ? 'val-red' : 'val-gold'));
      document.getElementById('refPrice').textContent = 'Reference Price: $' + data.referencePrice.toFixed(2);

      document.getElementById('primaryPrice').textContent = '$' + data.primaryInstrument.price.toFixed(2);
      document.getElementById('hedgePrice').textContent = '$' + data.hedgeInstrument.price.toFixed(2);

      const pPos = data.portfolio.primaryPosition;
      document.getElementById('primaryPosition').textContent = pPos
        ? \`Position: \${pPos.side} \${pPos.quantityContracts} contracts ($ \${data.portfolio.primaryNotionalUsd} notional)\`
        : 'Position: Flat';

      document.getElementById('hedgeStats').textContent = \`Active Hedges: \${data.openHedges.length} | Realized Hedge Profit: $\${data.portfolio.realizedHedgeProfitTotalUsd}\`;

      const totalPnl = data.portfolio.combinedUnrealizedPnl + data.portfolio.totalRealizedPnl;
      const pnlEl = document.getElementById('totalPnl');
      pnlEl.textContent = (totalPnl >= 0 ? '+$' : '-$') + Math.abs(totalPnl).toFixed(2);
      pnlEl.className = 'card-value ' + (totalPnl >= 0 ? 'val-green' : 'val-red');

      document.getElementById('pnlDetails').textContent = \`Unrealized: $\${data.portfolio.combinedUnrealizedPnl.toFixed(2)} | Realized: $\${data.portfolio.totalRealizedPnl.toFixed(2)}\`;

      // Render Grid table
      const gridBody = document.getElementById('gridTableBody');
      if (data.gridLevels && data.gridLevels.length > 0) {
        gridBody.innerHTML = data.gridLevels.map(g => \`
          <tr>
            <td>Level \${g.level}</td>
            <td>$\${g.triggerPrice.toFixed(2)}</td>
            <td>\${g.quantity}</td>
            <td style="color:\${g.status === 'FILLED' ? 'var(--accent-green)' : 'var(--text-muted)'}">\${g.status}</td>
          </tr>
        \`).join('');
      } else {
        gridBody.innerHTML = '<tr><td colspan="4" style="color:var(--text-muted); text-align:center;">No active grid orders</td></tr>';
      }

      // Render Hedge table
      const hedgeBody = document.getElementById('hedgeTableBody');
      if (data.openHedges && data.openHedges.length > 0) {
        hedgeBody.innerHTML = data.openHedges.map(h => \`
          <tr>
            <td>Level \${h.triggerLevel}</td>
            <td><span style="color:\${h.side === 'LONG' ? 'var(--accent-green)' : 'var(--accent-red)'}">\${h.side}</span></td>
            <td>$\${h.entryPrice.toFixed(2)}</td>
            <td>$\${h.targetPrice.toFixed(2)}</td>
            <td>\${h.quantityContracts}</td>
            <td style="color:var(--accent-gold)">\${h.status}</td>
          </tr>
        \`).join('');
      } else {
        hedgeBody.innerHTML = '<tr><td colspan="6" style="color:var(--text-muted); text-align:center;">No active hedges</td></tr>';
      }
    }

    function updateTick(data) {
      if (data.primaryPrice) document.getElementById('primaryPrice').textContent = '$' + data.primaryPrice.toFixed(2);
      if (data.hedgePrice) document.getElementById('hedgePrice').textContent = '$' + data.hedgePrice.toFixed(2);
    }

    async function sendAction(action) {
      try {
        const resp = await fetch('/api/strategy/' + action, { method: 'POST' });
        const res = await resp.json();
        log('Action [' + action + ']: ' + (res.message || 'Done'));
      } catch (err) {
        log('Error triggering ' + action + ': ' + err.message);
      }
    }

    window.onload = initWebSocket;
  </script>
</body>
</html>`;
  }
}

