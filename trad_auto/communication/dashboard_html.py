# ruff: noqa: E501
"""HTML & JavaScript frontend template for Trad-Auto Live Web Dashboard."""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trad-Auto &bull; Institutional Quantitative Trading Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#f0fdf4',
              500: '#22c55e',
              900: '#14532d',
            },
            surface: {
              dark: '#0b0e14',
              card: '#121824',
              cardHover: '#182132',
              border: '#1f293d',
            }
          }
        }
      }
    }
  </script>
  <style>
    @keyframes pulse-subtle {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.6; }
    }
    .pulse-live { animation: pulse-subtle 2s infinite ease-in-out; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0b0e14; }
    ::-webkit-scrollbar-thumb { background: #1f293d; border-radius: 3px; }
  </style>
</head>
<body class="bg-surface-dark text-slate-100 font-sans antialiased min-h-screen selection:bg-emerald-500 selection:text-white">

  <!-- Navigation Bar -->
  <header class="border-b border-surface-border bg-surface-card/80 backdrop-blur sticky top-0 z-50 px-6 py-3.5 flex flex-wrap items-center justify-between gap-4">
    <div class="flex items-center gap-3">
      <div class="h-9 w-9 rounded-lg bg-gradient-to-tr from-emerald-500 to-cyan-500 flex items-center justify-center font-black text-black text-lg shadow-lg shadow-emerald-500/20">
        &Sigma;
      </div>
      <div>
        <div class="flex items-center gap-2">
          <h1 class="font-extrabold text-base tracking-tight text-white">TRAD-AUTO</h1>
          <span class="text-[10px] uppercase font-bold tracking-widest px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Institutional Quant</span>
        </div>
        <p class="text-xs text-slate-400 font-mono">BTCUSDT Smart Money Scalper &bull; Zero-Gap Live Engine</p>
      </div>
    </div>

    <!-- Status Badges & Live Controls -->
    <div class="flex items-center gap-3">
      <div id="mode-badge" class="px-2.5 py-1 rounded-md text-xs font-mono font-bold tracking-wide border flex items-center gap-1.5 bg-slate-800 text-slate-300 border-slate-700">
        <span class="h-2 w-2 rounded-full bg-slate-400"></span>
        <span id="mode-text">INITIALIZING...</span>
      </div>

      <div id="session-badge" class="px-2.5 py-1 rounded-md text-xs font-mono font-bold tracking-wide border flex items-center gap-1.5 bg-emerald-950/60 text-emerald-400 border-emerald-500/30">
        <span class="h-2 w-2 rounded-full bg-emerald-500 pulse-live"></span>
        <span id="session-text">CHECKING</span>
      </div>

      <div class="h-6 w-px bg-surface-border mx-1"></div>

      <!-- Quick Action Buttons -->
      <button onclick="sendCommand('pause')" title="Pause new trade entries" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-amber-500/10 text-amber-300 border border-amber-500/20 hover:bg-amber-500/20 transition active:scale-95">
        Pause
      </button>
      <button onclick="sendCommand('resume')" title="Resume authorized trading" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 hover:bg-emerald-500/20 transition active:scale-95">
        Resume
      </button>
      <button onclick="confirmKillSwitch()" title="Emergency Kill Switch: Flatten all & Cancel orders" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-500/20 text-rose-300 border border-rose-500/40 hover:bg-rose-500/30 transition active:scale-95 flex items-center gap-1">
        <span>🚨</span> Kill Switch
      </button>
    </div>
  </header>

  <!-- Main Container -->
  <main class="max-w-7xl mx-auto px-6 py-6 space-y-6">

    <!-- KPI Metric Cards -->
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
      <!-- USDT Balance -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-4 shadow-sm">
        <div class="flex items-center justify-between text-xs text-slate-400 uppercase font-semibold">
          <span>USDT Balance</span>
          <span class="text-slate-500 font-mono">Ledger</span>
        </div>
        <div class="mt-2 text-2xl font-black text-white font-mono tracking-tight" id="kpi-balance">$0.00</div>
        <div class="mt-1 text-xs text-slate-400 flex items-center justify-between">
          <span>Available Margin:</span>
          <span class="font-mono text-slate-300" id="kpi-available-margin">$0.00</span>
        </div>
      </div>

      <!-- Today Realized P&L -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-4 shadow-sm">
        <div class="flex items-center justify-between text-xs text-slate-400 uppercase font-semibold">
          <span>Today Realized P&L</span>
          <span class="text-slate-500 font-mono">Net</span>
        </div>
        <div class="mt-2 text-2xl font-black font-mono tracking-tight" id="kpi-realized-pnl">$0.00</div>
        <div class="mt-1 text-xs text-slate-400 flex items-center justify-between">
          <span>Target / Breaker:</span>
          <span class="font-mono text-slate-300" id="kpi-pnl-target">Active</span>
        </div>
      </div>

      <!-- Open Unrealized P&L -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-4 shadow-sm">
        <div class="flex items-center justify-between text-xs text-slate-400 uppercase font-semibold">
          <span>Unrealized P&L</span>
          <span class="text-slate-500 font-mono">Live Mark</span>
        </div>
        <div class="mt-2 text-2xl font-black font-mono tracking-tight" id="kpi-unrealized-pnl">$0.00</div>
        <div class="mt-1 text-xs text-slate-400 flex items-center justify-between">
          <span>Active Positions:</span>
          <span class="font-mono text-slate-300" id="kpi-open-count">0</span>
        </div>
      </div>

      <!-- Authorized Capital / Risk Budget -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-4 shadow-sm">
        <div class="flex items-center justify-between text-xs text-slate-400 uppercase font-semibold">
          <span>Deployed Capital</span>
          <span class="text-slate-500 font-mono">Exposure</span>
        </div>
        <div class="mt-2 text-2xl font-black text-cyan-400 font-mono tracking-tight" id="kpi-deployed-capital">$0.00</div>
        <div class="mt-1 text-xs text-slate-400 flex items-center justify-between">
          <span>Cap Ceiling:</span>
          <span class="font-mono text-slate-300" id="kpi-auth-cap">$10,000.00</span>
        </div>
      </div>

      <!-- Feed & Health Status -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-4 shadow-sm">
        <div class="flex items-center justify-between text-xs text-slate-400 uppercase font-semibold">
          <span>Feed & Health</span>
          <span class="text-slate-500 font-mono">Watchdog</span>
        </div>
        <div class="mt-2 text-2xl font-black font-mono tracking-tight text-emerald-400" id="kpi-health-status">HEALTHY</div>
        <div class="mt-1 text-xs text-slate-400 flex items-center justify-between">
          <span>Latency / Staleness:</span>
          <span class="font-mono text-emerald-400" id="kpi-staleness">&lt; 100ms &bull; FRESH</span>
        </div>
      </div>
    </section>

    <!-- Center Section: BTCUSDT Smart Money Scalper & Quant Alpha Radar -->
    <section class="grid grid-cols-1 lg:grid-cols-3 gap-6">

      <!-- Column 1: Market Regime & ADX Chop Filter -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-5 space-y-4">
        <div class="flex items-center justify-between border-b border-surface-border pb-3">
          <div class="flex items-center gap-2">
            <span class="text-lg">🛡️</span>
            <h3 class="font-bold text-sm tracking-wide text-white uppercase">ADX Regime & Chop Filter</h3>
          </div>
          <span id="adx-regime-pill" class="text-[11px] font-mono px-2 py-0.5 rounded font-bold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
            TRENDING (SAFE)
          </span>
        </div>

        <div class="space-y-3">
          <div>
            <div class="flex justify-between text-xs text-slate-400 mb-1">
              <span>ADX (14) Strength: <strong class="text-white font-mono" id="adx-val">28.4</strong></span>
              <span class="font-mono text-slate-400">Min Threshold: 22.0</span>
            </div>
            <div class="w-full bg-slate-800 h-2.5 rounded-full overflow-hidden">
              <div id="adx-bar" class="bg-emerald-500 h-2.5 rounded-full transition-all duration-500" style="width: 56%"></div>
            </div>
            <p class="text-[11px] text-slate-400 mt-1">If ADX &lt; 22.0, market is in horizontal chop; Scalper blocks entries to protect capital.</p>
          </div>

          <div class="grid grid-cols-2 gap-2 pt-2 border-t border-surface-border">
            <div class="bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
              <span class="text-[10px] uppercase font-bold text-emerald-400">+DI (Bullish)</span>
              <div class="text-lg font-mono font-bold text-white mt-0.5" id="plus-di-val">27.6</div>
            </div>
            <div class="bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
              <span class="text-[10px] uppercase font-bold text-rose-400">-DI (Bearish)</span>
              <div class="text-lg font-mono font-bold text-white mt-0.5" id="minus-di-val">14.2</div>
            </div>
          </div>

          <!-- EMA Ribbon Status -->
          <div class="pt-2 border-t border-surface-border">
            <div class="flex items-center justify-between text-xs mb-1.5">
              <span class="text-slate-400">EMA Ribbon (9/21/50):</span>
              <span id="ema-ribbon-status" class="font-mono font-bold text-emerald-400">BULLISH EXPANSION</span>
            </div>
            <div class="flex gap-1.5 text-[11px] font-mono">
              <span class="px-2 py-1 bg-slate-900 rounded border border-surface-border flex-1 text-center" id="ema-9">EMA9: --</span>
              <span class="px-2 py-1 bg-slate-900 rounded border border-surface-border flex-1 text-center" id="ema-21">EMA21: --</span>
              <span class="px-2 py-1 bg-slate-900 rounded border border-surface-border flex-1 text-center" id="ema-50">EMA50: --</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Column 2: Qlib Alpha Factor & Microstructure Conviction -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-5 space-y-4">
        <div class="flex items-center justify-between border-b border-surface-border pb-3">
          <div class="flex items-center gap-2">
            <span class="text-lg">⚡</span>
            <h3 class="font-bold text-sm tracking-wide text-white uppercase">Qlib Alpha Conviction</h3>
          </div>
          <span id="qlib-score-pill" class="text-[11px] font-mono px-2 py-0.5 rounded font-bold uppercase bg-cyan-500/10 text-cyan-400 border border-cyan-500/30">
            COMPOSITE +0.68
          </span>
        </div>

        <div class="space-y-3">
          <div>
            <div class="flex justify-between text-xs text-slate-400 mb-1">
              <span>Alpha Conviction Score:</span>
              <span class="font-mono font-bold text-white" id="qlib-score-text">+0.68 (Bullish Conviction)</span>
            </div>
            <div class="w-full bg-slate-800 h-2.5 rounded-full overflow-hidden flex">
              <div class="bg-rose-500 h-2.5 transition-all" style="width: 20%"></div>
              <div class="bg-slate-700 h-2.5 transition-all" style="width: 20%"></div>
              <div class="bg-emerald-500 h-2.5 transition-all" style="width: 60%"></div>
            </div>
          </div>

          <!-- Microstructure Factors -->
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
              <div class="text-[10px] text-slate-400 uppercase">Momentum ROC</div>
              <div class="font-mono font-bold text-emerald-400 text-sm mt-0.5" id="qlib-mom">+0.54</div>
            </div>
            <div class="bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
              <div class="text-[10px] text-slate-400 uppercase">Volume Flow</div>
              <div class="font-mono font-bold text-emerald-400 text-sm mt-0.5" id="qlib-vol">+0.62</div>
            </div>
            <div class="bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
              <div class="text-[10px] text-slate-400 uppercase">Wick Absorption</div>
              <div class="font-mono font-bold text-emerald-400 text-sm mt-0.5" id="qlib-wick">+0.48</div>
            </div>
            <div class="bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
              <div class="text-[10px] text-slate-400 uppercase">Volatility Range</div>
              <div class="font-mono font-bold text-cyan-400 text-sm mt-0.5" id="qlib-volat">+0.32</div>
            </div>
          </div>

          <!-- Whale Flow & Taker Volume -->
          <div class="pt-2 border-t border-surface-border flex items-center justify-between text-xs">
            <span class="text-slate-400">Binance Whale Flow:</span>
            <span class="font-mono font-bold text-emerald-400 flex items-center gap-1" id="whale-ratio">
              <span>🐋</span> Top Traders: 68% Long
            </span>
          </div>

          <!-- Quant ML Model Conviction Gate -->
          <div class="pt-2 border-t border-surface-border space-y-1.5">
            <div class="flex items-center justify-between text-xs">
              <span class="text-slate-400 flex items-center gap-1">
                <span>🤖</span> Quant ML Conviction:
              </span>
              <span id="ml-gate-badge" class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                ACTIVE (≥65%)
              </span>
            </div>
            <div class="flex justify-between items-center bg-slate-900/60 p-2 rounded-lg border border-surface-border text-xs">
              <span class="text-slate-400">Triple-Barrier Prob:</span>
              <span class="font-mono font-bold text-white text-sm" id="ml-prob-val">Calibrated & Scanning</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Column 3: News Volatility Shield & Macro Alerts -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-5 space-y-4">
        <div class="flex items-center justify-between border-b border-surface-border pb-3">
          <div class="flex items-center gap-2">
            <span class="text-lg">🌐</span>
            <h3 class="font-bold text-sm tracking-wide text-white uppercase">News Volatility Shield</h3>
          </div>
          <span id="news-blackout-pill" class="text-[11px] font-mono px-2 py-0.5 rounded font-bold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
            ALL CLEAR
          </span>
        </div>

        <div class="space-y-3">
          <div class="flex items-center justify-between text-xs bg-slate-900/60 p-2.5 rounded-lg border border-surface-border">
            <span class="text-slate-400">Shield Protection:</span>
            <span class="font-mono font-bold text-emerald-400" id="news-shield-state">ACTIVE (15m Pre/Post Buffer)</span>
          </div>

          <div>
            <div class="text-xs text-slate-400 uppercase font-semibold mb-1.5">Upcoming Macro Events</div>
            <div id="macro-events-list" class="space-y-1.5 text-xs">
              <div class="bg-slate-900/40 p-2 rounded border border-surface-border flex justify-between items-center">
                <span class="text-slate-300">FOMC Rate Decision</span>
                <span class="font-mono text-cyan-400 text-[11px]">In 3 days</span>
              </div>
              <div class="bg-slate-900/40 p-2 rounded border border-surface-border flex justify-between items-center">
                <span class="text-slate-300">US CPI Release</span>
                <span class="font-mono text-slate-400 text-[11px]">Oct 10</span>
              </div>
            </div>
          </div>

          <div>
            <div class="text-xs text-slate-400 uppercase font-semibold mb-1.5">Live Crypto Headlines (NLP Analyzed)</div>
            <div id="headlines-list" class="space-y-1 text-xs">
              <div class="text-slate-300 truncate">&bull; Bitcoin ETF inflows surge past $400M daily</div>
              <div class="text-slate-300 truncate">&bull; Fed reiterates gradual rate easing cycle</div>
            </div>
          </div>
        </div>
      </div>

    </section>

    <!-- Bottom Section: Open Positions & Active Protective Orders -->
    <section class="grid grid-cols-1 lg:grid-cols-2 gap-6">

      <!-- Open Positions Table -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-5 space-y-4">
        <div class="flex items-center justify-between border-b border-surface-border pb-3">
          <div class="flex items-center gap-2">
            <span class="text-lg">📊</span>
            <h3 class="font-bold text-sm tracking-wide text-white uppercase">Open Positions &amp; Trailing Stops</h3>
          </div>
          <span class="text-xs font-mono text-slate-400">Atomic Shield Guard Active</span>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs font-mono">
            <thead>
              <tr class="text-slate-400 border-b border-surface-border/60 uppercase text-[10px]">
                <th class="py-2 font-semibold">Symbol</th>
                <th class="py-2 font-semibold">Side</th>
                <th class="py-2 font-semibold">Size</th>
                <th class="py-2 font-semibold">Entry</th>
                <th class="py-2 font-semibold">Mark</th>
                <th class="py-2 font-semibold">Unrealized P&L</th>
                <th class="py-2 font-semibold">Trailing Stop</th>
              </tr>
            </thead>
            <tbody id="positions-tbody" class="divide-y divide-surface-border/40">
              <tr>
                <td colspan="7" class="py-6 text-center text-slate-500 font-sans text-xs">
                  No active open positions. Scalper is scanning BTCUSDT order flow...
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Active Protective Orders Table -->
      <div class="bg-surface-card border border-surface-border rounded-xl p-5 space-y-4">
        <div class="flex items-center justify-between border-b border-surface-border pb-3">
          <div class="flex items-center gap-2">
            <span class="text-lg">🎯</span>
            <h3 class="font-bold text-sm tracking-wide text-white uppercase">Active Orders &amp; Auto-Reaper</h3>
          </div>
          <span class="text-xs font-mono text-slate-400">Post-Only &bull; reduceOnly Brackets</span>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs font-mono">
            <thead>
              <tr class="text-slate-400 border-b border-surface-border/60 uppercase text-[10px]">
                <th class="py-2 font-semibold">Order ID</th>
                <th class="py-2 font-semibold">Symbol</th>
                <th class="py-2 font-semibold">Type</th>
                <th class="py-2 font-semibold">Side</th>
                <th class="py-2 font-semibold">Price</th>
                <th class="py-2 font-semibold">Qty</th>
                <th class="py-2 font-semibold">Purpose</th>
              </tr>
            </thead>
            <tbody id="orders-tbody" class="divide-y divide-surface-border/40">
              <tr>
                <td colspan="7" class="py-6 text-center text-slate-500 font-sans text-xs">
                  Zero resting unshielded orders.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </section>

    <!-- Terminal Command Bar -->
    <section class="bg-surface-card border border-surface-border rounded-xl p-4 space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center gap-2 text-xs font-mono text-slate-400">
          <span class="text-emerald-400">&gt;_</span>
          <span>Trad-Auto Command Terminal:</span>
          <input id="cmd-input" type="text" placeholder="Type command: status, pause, resume, pnl, positions..." class="bg-slate-900 border border-surface-border rounded-lg px-3 py-1.5 text-xs text-white font-mono w-80 focus:outline-none focus:border-emerald-500" onkeydown="if(event.key==='Enter') executeCustomCommand()">
          <button onclick="executeCustomCommand()" class="px-3 py-1.5 text-xs font-semibold rounded-lg bg-emerald-500 text-black hover:bg-emerald-400 transition font-bold">Execute</button>
        </div>

        <div class="text-xs text-slate-500 font-mono flex items-center gap-3">
          <span>Refresh: <strong class="text-slate-300">1.5s live polling</strong></span>
          <span>&bull;</span>
          <span id="last-sync-time">Last sync: Just now</span>
        </div>
      </div>
      <div id="terminal-feedback" class="hidden text-xs font-mono p-3 rounded-lg border transition-all"></div>
    </section>

  </main>

  <script>
    // Live Dashboard Telemetry Fetcher
    async function fetchStatus() {
      try {
        const resp = await fetch('/api/status');
        if (!resp.ok) return;
        const data = await resp.json();
        updateDashboardUI(data);
      } catch (err) {
        console.debug('Dashboard polling background error:', err);
      }
    }

    function updateDashboardUI(data) {
      if (!data) return;

      // Mode Badge
      const modeText = document.getElementById('mode-text');
      if (modeText) modeText.textContent = data.trading_mode || 'PAPER';

      // Session State Badge
      const sessionText = document.getElementById('session-text');
      const sessionBadge = document.getElementById('session-badge');
      if (sessionText && data.session_state) {
        sessionText.textContent = data.session_state;
        if (data.session_state === 'TRADING') {
          sessionBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-bold tracking-wide border flex items-center gap-1.5 bg-emerald-950/60 text-emerald-400 border-emerald-500/30';
        } else if (data.session_state === 'PAUSED') {
          sessionBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-bold tracking-wide border flex items-center gap-1.5 bg-amber-950/60 text-amber-400 border-amber-500/30';
        } else if (data.session_state === 'RISK_LOCKED' || data.session_state === 'EMERGENCY_STOP') {
          sessionBadge.className = 'px-2.5 py-1 rounded-md text-xs font-mono font-bold tracking-wide border flex items-center gap-1.5 bg-rose-950/60 text-rose-400 border-rose-500/30';
        }
      }

      // Balance & P&L
      const balEl = document.getElementById('kpi-balance');
      if (balEl && data.usdt_balance) balEl.textContent = '$' + parseFloat(data.usdt_balance).toLocaleString('en-US', { minimumFractionDigits: 2 });

      const realPnlEl = document.getElementById('kpi-realized-pnl');
      if (realPnlEl && data.today_realized_pnl !== undefined) {
        const pnl = parseFloat(data.today_realized_pnl);
        realPnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
        realPnlEl.className = 'mt-2 text-2xl font-black font-mono tracking-tight ' + (pnl >= 0 ? 'text-emerald-400' : 'text-rose-400');
      }

      const unrealPnlEl = document.getElementById('kpi-unrealized-pnl');
      if (unrealPnlEl && data.current_unrealized_pnl !== undefined) {
        const upnl = parseFloat(data.current_unrealized_pnl);
        unrealPnlEl.textContent = (upnl >= 0 ? '+$' : '-$') + Math.abs(upnl).toFixed(2);
        unrealPnlEl.className = 'mt-2 text-2xl font-black font-mono tracking-tight ' + (upnl >= 0 ? 'text-emerald-400' : 'text-rose-400');
      }

      const countEl = document.getElementById('kpi-open-count');
      if (countEl) countEl.textContent = data.open_positions_count || 0;

      const deployedEl = document.getElementById('kpi-deployed-capital');
      if (deployedEl && data.total_deployed_capital) {
        deployedEl.textContent = '$' + parseFloat(data.total_deployed_capital).toFixed(2);
      }

      // Positions Table
      const posTbody = document.getElementById('positions-tbody');
      if (posTbody && data.open_positions) {
        if (data.open_positions.length === 0) {
          posTbody.innerHTML = '<tr><td colspan="7" class="py-6 text-center text-slate-500 font-sans text-xs">No active open positions. Scalper is scanning BTCUSDT order flow...</td></tr>';
        } else {
          posTbody.innerHTML = data.open_positions.map(p => {
            const sideColor = p.side === 'LONG' ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10';
            const pnlVal = parseFloat(p.unrealized_pnl || '0');
            const pnlColor = pnlVal >= 0 ? 'text-emerald-400' : 'text-rose-400';
            const trailing = data.trailing_info && data.trailing_info[p.symbol];
            const stopPrice = trailing && trailing.active_stop_price ? '$' + parseFloat(trailing.active_stop_price).toFixed(1) : 'Resting';

            return `<tr class="hover:bg-surface-cardHover/60 transition">
              <td class="py-2.5 font-bold text-white">${p.symbol}</td>
              <td class="py-2.5"><span class="px-2 py-0.5 rounded text-[10px] font-bold ${sideColor}">${p.side}</span></td>
              <td class="py-2.5 text-slate-300">${p.quantity}</td>
              <td class="py-2.5 text-slate-400">$${parseFloat(p.entry_price).toFixed(1)}</td>
              <td class="py-2.5 text-white font-bold">$${parseFloat(p.mark_price).toFixed(1)}</td>
              <td class="py-2.5 font-bold ${pnlColor}">${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(2)}</td>
              <td class="py-2.5 text-emerald-400 flex items-center gap-1 font-bold">
                <span>🛡️</span> ${stopPrice}
              </td>
            </tr>`;
          }).join('');
        }
      }

      // Active Orders Table
      const ordersTbody = document.getElementById('orders-tbody');
      if (ordersTbody && data.active_orders) {
        if (data.active_orders.length === 0) {
          ordersTbody.innerHTML = '<tr><td colspan="7" class="py-6 text-center text-slate-500 font-sans text-xs">Zero resting unshielded orders.</td></tr>';
        } else {
          ordersTbody.innerHTML = data.active_orders.map(o => {
            const sideColor = o.side === 'BUY' ? 'text-emerald-400' : 'text-rose-400';
            return `<tr class="hover:bg-surface-cardHover/60 transition">
              <td class="py-2 text-slate-400 text-[11px]">${o.client_order_id || o.order_id}</td>
              <td class="py-2 text-white font-semibold">${o.symbol}</td>
              <td class="py-2 text-slate-300">${o.order_type}</td>
              <td class="py-2 font-bold ${sideColor}">${o.side}</td>
              <td class="py-2 text-white">$${parseFloat(o.price).toFixed(1)}</td>
              <td class="py-2 text-slate-300">${o.quantity}</td>
              <td class="py-2 text-cyan-400 text-[10px] uppercase font-bold">${o.action_purpose}</td>
            </tr>`;
          }).join('');
        }
      }

      // Strategy Snapshot (ADX, RSI, EMA Ribbon)
      if (data.strategy_info && data.strategy_info.smart_money_scalper) {
        const s = data.strategy_info.smart_money_scalper;
        if (s.adx) {
          document.getElementById('adx-val').textContent = parseFloat(s.adx).toFixed(1);
          const barWidth = Math.min(100, (parseFloat(s.adx) / 50.0) * 100);
          document.getElementById('adx-bar').style.width = barWidth + '%';
        }
        if (s.plus_di) document.getElementById('plus-di-val').textContent = parseFloat(s.plus_di).toFixed(1);
        if (s.minus_di) document.getElementById('minus-di-val').textContent = parseFloat(s.minus_di).toFixed(1);
        if (s.fast_ema) document.getElementById('ema-9').textContent = 'EMA9: $' + parseFloat(s.fast_ema).toFixed(1);
        if (s.slow_ema) document.getElementById('ema-21').textContent = 'EMA21: $' + parseFloat(s.slow_ema).toFixed(1);
        if (s.trend_ema) document.getElementById('ema-50').textContent = 'EMA50: $' + parseFloat(s.trend_ema).toFixed(1);

        // ML Model Conviction Gate
        if (s.is_ml_active !== undefined) {
          const mlBadge = document.getElementById('ml-gate-badge');
          const mlProb = document.getElementById('ml-prob-val');
          if (mlBadge && mlProb) {
            if (s.is_ml_active) {
              if (s.ml_probability) {
                const probVal = parseFloat(s.ml_probability) * 100;
                mlProb.textContent = probVal.toFixed(1) + '%';
                if (probVal >= 65.0) {
                  mlBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30';
                  mlBadge.textContent = 'PERMITTED (≥65%)';
                  mlProb.className = 'font-mono font-bold text-emerald-400 text-sm';
                } else {
                  mlBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-500/10 text-amber-400 border border-amber-500/30';
                  mlBadge.textContent = 'BLOCKED (<65%)';
                  mlProb.className = 'font-mono font-bold text-amber-400 text-sm';
                }
              } else {
                mlBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-500/10 text-cyan-400 border border-cyan-500/30';
                mlBadge.textContent = 'ACTIVE (≥65%)';
                mlProb.textContent = 'Calibrated & Scanning';
                mlProb.className = 'font-mono font-bold text-cyan-300 text-sm';
              }
            } else {
              mlBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-400 border border-slate-700';
              mlBadge.textContent = 'OFFLINE';
              mlProb.textContent = 'Heuristic Only';
              mlProb.className = 'font-mono font-bold text-slate-400 text-sm';
            }
          }
        }
      }

      const syncEl = document.getElementById('last-sync-time');
      if (syncEl) syncEl.textContent = 'Last sync: ' + new Date().toLocaleTimeString();
    }

    async function sendCommand(cmd) {
      const fb = document.getElementById('terminal-feedback');
      if (fb) {
        fb.className = 'text-xs font-mono p-3 rounded-lg border bg-slate-900 text-cyan-300 border-cyan-500/30 flex items-center gap-2';
        fb.innerHTML = '<span>⏳</span> Executing: <strong>' + cmd + '</strong>...';
        fb.classList.remove('hidden');
      }

      try {
        const resp = await fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: cmd })
        });
        const res = await resp.json();
        const msg = res.message || (res.success ? 'Command executed successfully.' : res.error);
        if (fb) {
          if (res.success) {
            fb.className = 'text-xs font-mono p-3 rounded-lg border bg-emerald-950/60 text-emerald-300 border-emerald-500/30 flex items-center justify-between';
            fb.innerHTML = '<span>✅ ' + msg + '</span><button onclick="this.parentElement.classList.add(\\'hidden\\')" class="text-slate-400 hover:text-white">&times;</button>';
          } else {
            fb.className = 'text-xs font-mono p-3 rounded-lg border bg-amber-950/60 text-amber-300 border-amber-500/30 flex items-center justify-between';
            fb.innerHTML = '<span>⚠️ ' + msg + '</span><button onclick="this.parentElement.classList.add(\\'hidden\\')" class="text-slate-400 hover:text-white">&times;</button>';

            // Auto-populate input if confirmation code is suggested
            const match = msg.match(/Reply\\s+['"](confirm[^'"]+)['"]/i);
            if (match && match[1]) {
              const inp = document.getElementById('cmd-input');
              if (inp) {
                inp.value = match[1];
                inp.focus();
              }
            }
          }
        }
        fetchStatus();
      } catch (err) {
        if (fb) {
          fb.className = 'text-xs font-mono p-3 rounded-lg border bg-rose-950/60 text-rose-300 border-rose-500/30 flex items-center justify-between';
          fb.innerHTML = '<span>❌ Error: ' + err + '</span><button onclick="this.parentElement.classList.add(\\'hidden\\')" class="text-slate-400 hover:text-white">&times;</button>';
        }
      }
    }

    function confirmKillSwitch() {
      if (confirm('🚨 ARE YOU ABSOLUTELY SURE?\\n\\nThis will engage the EMERGENCY KILL SWITCH, instantly cancel all active orders, and market FLATTEN all open positions.')) {
        sendCommand('kill');
      }
    }

    function executeCustomCommand() {
      const input = document.getElementById('cmd-input');
      if (!input || !input.value.trim()) return;
      const cmd = input.value.trim();
      input.value = '';
      sendCommand(cmd);
    }

    // Auto-refresh every 1.5 seconds
    setInterval(fetchStatus, 1500);
    fetchStatus();
  </script>
</body>
</html>
"""
