"""
dashboard_server.py — FastAPI real-time dashboard server.
Reads logs/paper_state.json every 2 seconds and pushes
updates via WebSocket to all connected browsers.

Run:  venv/bin/python dashboard_server.py
Open: http://127.0.0.1:8765
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from autopilot import Autopilot

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATE_FILE   = Path("logs/paper_state.json")
TUNE_FILE    = Path("logs/tune_directive.json")
PERF_FILE    = Path("logs/autopilot_targets.json")
PERF_LOG     = Path("logs/performance_log.json")

app = FastAPI(title="Gold AI Trader Dashboard")
autopilot = Autopilot()

# ── Connected WebSocket clients ───────────────────────────────────────────────
clients: Set[WebSocket] = set()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def _build_payload() -> dict:
    state   = _read_json(STATE_FILE)
    tune    = _read_json(TUNE_FILE)
    ap_snap = _read_json(PERF_FILE)
    perf_h  = []
    try:
        if PERF_LOG.exists():
            perf_h = json.loads(PERF_LOG.read_text())[-50:]
    except Exception:
        pass

    trades  = state.get("recent_trades", [])
    account = state.get("account", {})
    metrics = ap_snap.get("metrics", {})

    # Equity curve from perf history
    equity_curve = [
        {"t": p["timestamp"][:16], "v": p["metrics"].get("win_rate", 0) * 100}
        for p in perf_h if "metrics" in p
    ]

    return {
        "ts":              datetime.utcnow().isoformat(),
        "spot_price":      state.get("spot_price", 0),
        "is_active":       state.get("is_active", False),
        "circuit_breaker": state.get("circuit_breaker", False),
        "account":         account,
        "positions":       state.get("positions", []),
        "recent_trades":   trades[-20:],
        "learner_weights": state.get("learner_weights", {}),
        "learner_iters":   state.get("learner_iterations", 0),
        "tick_count":      state.get("tick_count", 0),
        "targets":         autopilot.get_targets_display(metrics),
        "ap_metrics":      metrics,
        "ap_scores":       ap_snap.get("scores", {}),
        "ap_overall":      ap_snap.get("overall_score", 0),
        "ap_directive":    tune,
        "ap_status":       ap_snap.get("status", {}),
        "equity_curve":    equity_curve,
        "perf_history":    perf_h[-10:],
    }


# ── WebSocket broadcast loop ──────────────────────────────────────────────────
async def _broadcast_loop():
    prev_trade_count = 0
    while True:
        try:
            payload = _build_payload()

            # Trigger autopilot evaluation when new trades arrive
            trades = payload["recent_trades"]
            if len(trades) != prev_trade_count:
                prev_trade_count = len(trades)
                state   = _read_json(STATE_FILE)
                account = state.get("account", {})
                closed  = [t for t in trades if t.get("status") == "CLOSED"]
                result  = autopilot.evaluate(closed, account)
                if result:
                    payload["targets"]      = autopilot.get_targets_display(result["metrics"])
                    payload["ap_metrics"]   = result["metrics"]
                    payload["ap_scores"]    = result["scores"]
                    payload["ap_overall"]   = result["overall_score"]
                    payload["ap_directive"] = result["directive"]
                    payload["ap_status"]    = result["status"]

            msg = json.dumps(payload)
            dead = set()
            for ws in list(clients):
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.add(ws)
            clients.difference_update(dead)
        except Exception as e:
            logger.error(f"[broadcast] {e}")
        await asyncio.sleep(2)


@app.on_event("startup")
async def startup():
    Path("logs").mkdir(exist_ok=True)
    asyncio.create_task(_broadcast_loop())
    logger.info("Dashboard server started — http://127.0.0.1:8765")


# ── HTTP endpoints ────────────────────────────────────────────────────────────
@app.get("/api/state")
async def api_state():
    return JSONResponse(_build_payload())


@app.get("/api/targets")
async def api_targets():
    snap = _read_json(PERF_FILE)
    return JSONResponse({
        "targets":  autopilot.get_targets_display(snap.get("metrics")),
        "overall":  snap.get("overall_score", 0),
        "directive": _read_json(TUNE_FILE),
    })


@app.post("/api/command")
async def api_command(body: dict):
    cmd_file = Path("data/command_queue.json")
    cmd_file.parent.mkdir(exist_ok=True)
    existing = []
    if cmd_file.exists():
        try:
            existing = json.loads(cmd_file.read_text())
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:
            existing = []
    existing.append(body)
    cmd_file.write_text(json.dumps(existing))
    return {"status": "queued", "command": body}


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    logger.info(f"[WS] client connected  (total={len(clients)})")
    try:
        # Send current state immediately on connect
        await ws.send_text(json.dumps(_build_payload()))
        while True:
            await ws.receive_text()   # keep alive / receive ping
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
        logger.info(f"[WS] client disconnected  (total={len(clients)})")


# ── Dashboard HTML ────────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gold AI Trader — Live Dashboard</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg:       #0d1117;
    --card:     #161b22;
    --border:   #30363d;
    --gold:     #f0b429;
    --green:    #3fb950;
    --red:      #f85149;
    --blue:     #58a6ff;
    --purple:   #bc8cff;
    --muted:    #8b949e;
    --text:     #e6edf3;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', monospace; font-size: 13px; }

  /* ── Layout ── */
  .topbar { display: flex; align-items: center; justify-content: space-between;
            padding: 10px 20px; background: var(--card); border-bottom: 1px solid var(--border); }
  .topbar-left { display: flex; align-items: center; gap: 16px; }
  .logo { font-size: 16px; font-weight: 700; color: var(--gold); letter-spacing: 1px; }
  .price-badge { font-size: 22px; font-weight: 700; color: var(--gold); }
  .badge { padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge-green { background: rgba(63,185,80,.15); color: var(--green); border: 1px solid rgba(63,185,80,.3); }
  .badge-red   { background: rgba(248,81,73,.15);  color: var(--red);   border: 1px solid rgba(248,81,73,.3); }
  .badge-gold  { background: rgba(240,180,41,.15); color: var(--gold);  border: 1px solid rgba(240,180,41,.3); }

  .main { display: grid; grid-template-columns: 1fr 340px; grid-template-rows: auto 1fr; gap: 12px; padding: 12px; height: calc(100vh - 52px); }
  .col-left  { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .col-right { display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
  .card-title { font-size: 11px; font-weight: 700; color: var(--muted); text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }

  /* ── Stat row ── */
  .stats-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
  .stat-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 4px; }
  .stat-value { font-size: 20px; font-weight: 700; }

  /* ── Chart ── */
  #equity-chart { width: 100%; height: 220px; }

  /* ── Targets ── */
  .target-row { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; }
  .target-label { width: 100px; font-size: 11px; color: var(--muted); }
  .target-bar-bg { flex: 1; height: 7px; background: #21262d; border-radius: 4px; overflow: hidden; }
  .target-bar    { height: 100%; border-radius: 4px; transition: width .4s ease; }
  .target-vals   { width: 110px; text-align: right; font-size: 11px; }
  .target-status { width: 18px; font-size: 12px; }

  /* ── Gauge ring ── */
  .gauge-wrap { display: flex; flex-direction: column; align-items: center; margin: 4px 0 8px; }
  svg.gauge { width: 110px; height: 64px; }

  /* ── Learner weights ── */
  .weight-bar { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; }
  .weight-name { width: 70px; font-size: 10px; color: var(--muted); }
  .weight-fill { height: 8px; border-radius: 3px; background: var(--blue); transition: width .4s ease; min-width: 2px; }
  .weight-pct  { font-size: 10px; color: var(--text); width: 38px; text-align: right; }

  /* ── Directive panel ── */
  .directive-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .dir-item { background: #21262d; border-radius: 6px; padding: 6px 10px; }
  .dir-key  { font-size: 10px; color: var(--muted); }
  .dir-val  { font-size: 14px; font-weight: 700; color: var(--gold); }

  /* ── Positions / Trades table ── */
  .tbl { width: 100%; border-collapse: collapse; font-size: 11px; }
  .tbl th { color: var(--muted); font-weight: 600; text-align: left; padding: 4px 6px;
             border-bottom: 1px solid var(--border); font-size: 10px; text-transform: uppercase; }
  .tbl td { padding: 5px 6px; border-bottom: 1px solid rgba(48,54,61,.4); }
  .tbl tr:last-child td { border-bottom: none; }
  .buy  { color: var(--green); font-weight: 700; }
  .sell { color: var(--red);   font-weight: 700; }
  .pnl-pos { color: var(--green); }
  .pnl-neg { color: var(--red);   }

  /* ── Footer ticker ── */
  #ticker-bar { font-size: 11px; color: var(--muted); padding: 6px 20px;
                border-top: 1px solid var(--border); background: var(--card); }

  /* ── Pulse dot ── */
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
           animation: pulse 1.5s infinite; display: inline-block; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.3; } }
</style>
</head>
<body>

<!-- ═══════════════════════ TOP BAR ═══════════════════════ -->
<div class="topbar">
  <div class="topbar-left">
    <span class="logo">⚡ GOLD AI TRADER</span>
    <span class="price-badge" id="spot">$-.--</span>
    <span id="price-chg" class="badge badge-gold">--</span>
    <span class="pulse" id="live-dot"></span>
    <span id="bot-status" class="badge badge-green">ACTIVE</span>
    <span class="badge" id="circuit-badge" style="display:none">🔴 CIRCUIT BREAKER</span>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <span style="color:var(--muted);font-size:11px">Ticks: <b id="tick-ct">0</b></span>
    <span style="color:var(--muted);font-size:11px">Learner: <b id="learn-it">0</b></span>
    <span id="ts" style="color:var(--muted);font-size:10px"></span>
  </div>
</div>

<!-- ═══════════════════════ MAIN GRID ════════════════════ -->
<div class="main">

  <!-- LEFT COLUMN -->
  <div class="col-left">

    <!-- Stats row -->
    <div class="stats-row">
      <div class="stat">
        <div class="stat-label">Balance</div>
        <div class="stat-value" id="balance">$--</div>
      </div>
      <div class="stat">
        <div class="stat-label">Equity</div>
        <div class="stat-value" id="equity">$--</div>
      </div>
      <div class="stat">
        <div class="stat-label">Unrealized P&L</div>
        <div class="stat-value" id="upnl">--</div>
      </div>
      <div class="stat">
        <div class="stat-label">Realized P&L</div>
        <div class="stat-value" id="rpnl">--</div>
      </div>
      <div class="stat">
        <div class="stat-label">Open Positions</div>
        <div class="stat-value" id="open-pos">0</div>
      </div>
    </div>

    <!-- Equity / P&L chart -->
    <div class="card" style="flex:1;min-height:240px">
      <div class="card-title">📈 Realized P&L Curve</div>
      <div id="equity-chart"></div>
    </div>

    <!-- Open Positions -->
    <div class="card">
      <div class="card-title">🟢 Open Positions</div>
      <table class="tbl">
        <thead><tr>
          <th>ID</th><th>Dir</th><th>Entry</th><th>Current</th>
          <th>Size</th><th>SL</th><th>TP</th><th>Unreal.PnL</th>
        </tr></thead>
        <tbody id="pos-body"><tr><td colspan="8" style="color:var(--muted);text-align:center">No open positions</td></tr></tbody>
      </table>
    </div>

    <!-- Trade Journal -->
    <div class="card" style="flex:1">
      <div class="card-title">📋 Trade Journal (last 15)</div>
      <table class="tbl">
        <thead><tr>
          <th>ID</th><th>Dir</th><th>Entry</th><th>Exit</th>
          <th>Size</th><th>PnL</th><th>Reason</th><th>Time</th>
        </tr></thead>
        <tbody id="trades-body"></tbody>
      </table>
    </div>

  </div><!-- /col-left -->

  <!-- RIGHT COLUMN -->
  <div class="col-right">

    <!-- Autopilot Overall Score -->
    <div class="card">
      <div class="card-title">🤖 Autopilot Overall Score</div>
      <div class="gauge-wrap">
        <svg class="gauge" viewBox="0 0 110 64">
          <path d="M 10 60 A 45 45 0 0 1 100 60" fill="none" stroke="#21262d" stroke-width="10" stroke-linecap="round"/>
          <path id="gauge-arc" d="M 10 60 A 45 45 0 0 1 100 60" fill="none" stroke="var(--gold)" stroke-width="10"
                stroke-linecap="round" stroke-dasharray="141.4" stroke-dashoffset="141.4" style="transition:stroke-dashoffset .6s ease"/>
        </svg>
        <div style="font-size:26px;font-weight:700;color:var(--gold);margin-top:-24px" id="gauge-pct">0%</div>
        <div style="font-size:10px;color:var(--muted)" id="gauge-label">Evaluating…</div>
      </div>
    </div>

    <!-- Performance Targets -->
    <div class="card">
      <div class="card-title">🎯 Performance Targets</div>
      <div id="targets-list"></div>
    </div>

    <!-- Autopilot Directive -->
    <div class="card">
      <div class="card-title">⚙️ Active Directive</div>
      <div class="directive-grid" id="directive-grid">
        <div class="dir-item"><div class="dir-key">Risk %</div><div class="dir-val" id="d-risk">1.5%</div></div>
        <div class="dir-item"><div class="dir-key">SL Mult</div><div class="dir-val" id="d-sl">1.5x</div></div>
        <div class="dir-item"><div class="dir-key">TP Mult</div><div class="dir-val" id="d-tp">3.0x</div></div>
        <div class="dir-item"><div class="dir-key">Cooldown</div><div class="dir-val" id="d-cool">30s</div></div>
        <div class="dir-item"><div class="dir-key">Max Pos</div><div class="dir-val" id="d-maxpos">1</div></div>
        <div class="dir-item"><div class="dir-key">Updated</div><div class="dir-val" id="d-ts" style="font-size:9px;color:var(--muted)">—</div></div>
      </div>
    </div>

    <!-- Learner Weights -->
    <div class="card">
      <div class="card-title">🧠 Learner Weights</div>
      <div id="weights-list"></div>
      <div style="margin-top:8px;font-size:10px;color:var(--muted)">
        Iterations: <b id="l-iters" style="color:var(--gold)">0</b>
      </div>
    </div>

    <!-- Performance Metrics -->
    <div class="card">
      <div class="card-title">📊 Latest Metrics</div>
      <div id="metrics-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:6px"></div>
    </div>

  </div><!-- /col-right -->

</div><!-- /main -->

<div id="ticker-bar">Connecting to live feed…</div>

<!-- ═══════════════════════ JAVASCRIPT ════════════════════ -->
<script>
// ── Equity chart setup ──────────────────────────────────────────────────────
const chartEl = document.getElementById('equity-chart');
const chart = LightweightCharts.createChart(chartEl, {
  width: chartEl.clientWidth, height: 200,
  layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
  grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  timeScale: { borderColor: '#30363d', timeVisible: true },
  rightPriceScale: { borderColor: '#30363d' },
});
const pnlSeries = chart.addLineSeries({
  color: '#3fb950', lineWidth: 2, priceLineVisible: false,
});
// Baseline
pnlSeries.setData([{ time: Math.floor(Date.now()/1000), value: 0 }]);

let prevPrice = 0;
let pnlHistory = [{ time: Math.floor(Date.now()/1000), value: 0 }];

// ── Gauge helper ────────────────────────────────────────────────────────────
function setGauge(pct) {
  const arc = document.getElementById('gauge-arc');
  const total = 141.4;
  arc.style.strokeDashoffset = total - (total * pct);
  arc.style.stroke = pct >= 0.75 ? '#3fb950' : pct >= 0.50 ? '#f0b429' : '#f85149';
  document.getElementById('gauge-pct').textContent = Math.round(pct * 100) + '%';
  document.getElementById('gauge-label').textContent =
    pct >= 0.75 ? '✅ On Track' : pct >= 0.50 ? '⚠️ Needs Work' : '❌ Off Target';
}

// ── Target bars ─────────────────────────────────────────────────────────────
function renderTargets(targets) {
  const el = document.getElementById('targets-list');
  el.innerHTML = '';
  targets.forEach(t => {
    const score = t.score;
    const color = score >= 0.9 ? 'var(--green)' : score >= 0.6 ? 'var(--gold)' : 'var(--red)';
    const label = t.lower_is_better
      ? `${t.value}${t.unit} → ≤${t.target}${t.unit}`
      : `${t.value}${t.unit} → ≥${t.target}${t.unit}`;
    el.innerHTML += `
      <div class="target-row">
        <span class="target-label">${t.label}</span>
        <div class="target-bar-bg">
          <div class="target-bar" style="width:${Math.round(score*100)}%;background:${color}"></div>
        </div>
        <span class="target-vals">${label}</span>
        <span class="target-status">${t.status.split(' ')[0]}</span>
      </div>`;
  });
}

// ── Learner weights ──────────────────────────────────────────────────────────
function renderWeights(weights) {
  const el = document.getElementById('weights-list');
  el.innerHTML = '';
  const entries = Object.entries(weights).sort((a,b) => b[1]-a[1]);
  const colors = ['#58a6ff','#bc8cff','#3fb950','#f0b429','#f85149'];
  entries.forEach(([k,v], i) => {
    el.innerHTML += `<div class="weight-bar">
      <span class="weight-name">${k}</span>
      <div class="weight-fill" style="width:${Math.round(v*200)}px;background:${colors[i%colors.length]}"></div>
      <span class="weight-pct">${(v*100).toFixed(1)}%</span>
    </div>`;
  });
}

// ── Metrics grid ─────────────────────────────────────────────────────────────
function renderMetrics(m) {
  const el = document.getElementById('metrics-grid');
  if (!m || !Object.keys(m).length) return;
  const fmt = {
    win_rate:      v => (v*100).toFixed(1)+'%',
    sharpe:        v => v.toFixed(2),
    max_drawdown:  v => (v*100).toFixed(2)+'%',
    avg_rr:        v => v.toFixed(2)+'x',
    profit_factor: v => v.toFixed(2)+'x',
  };
  el.innerHTML = Object.entries(m).map(([k,v]) => `
    <div style="background:#21262d;border-radius:6px;padding:6px 10px">
      <div style="font-size:10px;color:var(--muted)">${k.replace(/_/g,' ').toUpperCase()}</div>
      <div style="font-size:16px;font-weight:700;color:var(--gold)">${fmt[k] ? fmt[k](v) : v}</div>
    </div>`).join('');
}

// ── Positions table ──────────────────────────────────────────────────────────
function renderPositions(positions) {
  const tb = document.getElementById('pos-body');
  if (!positions.length) {
    tb.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:10px">No open positions</td></tr>';
    return;
  }
  tb.innerHTML = positions.map(p => {
    const pnlCls = p.unrealized_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    return `<tr>
      <td style="color:var(--muted)">${p.trade_id.slice(-6)}</td>
      <td class="${p.direction.toLowerCase()}">${p.direction}</td>
      <td>$${p.entry_price.toFixed(2)}</td>
      <td>$${p.current_price.toFixed(2)}</td>
      <td>${p.size} oz</td>
      <td style="color:var(--red)">$${p.sl.toFixed(2)}</td>
      <td style="color:var(--green)">$${p.tp.toFixed(2)}</td>
      <td class="${pnlCls}">${p.unrealized_pnl >= 0 ? '+' : ''}$${p.unrealized_pnl.toFixed(3)}</td>
    </tr>`;
  }).join('');
}

// ── Trades table ─────────────────────────────────────────────────────────────
function renderTrades(trades) {
  const tb = document.getElementById('trades-body');
  const closed = trades.filter(t => t.status === 'CLOSED').slice(-15).reverse();
  if (!closed.length) {
    tb.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:10px">No closed trades yet</td></tr>';
    return;
  }
  tb.innerHTML = closed.map(t => {
    const pnlCls = t.realized_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const sign   = t.realized_pnl >= 0 ? '+' : '';
    const time   = (t.exit_time || '').slice(11,16);
    return `<tr>
      <td style="color:var(--muted)">${t.trade_id.slice(-6)}</td>
      <td class="${t.direction.toLowerCase()}">${t.direction}</td>
      <td>$${t.entry_price.toFixed(2)}</td>
      <td>$${(t.exit_price||0).toFixed(2)}</td>
      <td>${t.size} oz</td>
      <td class="${pnlCls}">${sign}$${(t.realized_pnl||0).toFixed(4)}</td>
      <td style="font-size:10px;color:var(--muted)">${t.exit_reason||''}</td>
      <td style="color:var(--muted)">${time}</td>
    </tr>`;
  }).join('');
}

// ── Directive panel ──────────────────────────────────────────────────────────
function renderDirective(d) {
  if (!d || !Object.keys(d).length) return;
  document.getElementById('d-risk').textContent = ((d.risk_pct||.015)*100).toFixed(1)+'%';
  document.getElementById('d-sl').textContent   = (d.sl_atr_mult||1.5)+'x';
  document.getElementById('d-tp').textContent   = (d.tp_atr_mult||3.0)+'x';
  document.getElementById('d-cool').textContent = (d.cooldown_sec||30)+'s';
  document.getElementById('d-maxpos').textContent= d.max_positions||1;
  document.getElementById('d-ts').textContent   = (d.updated_at||'').slice(11,16)+' UTC';
}

// ── Main data handler ────────────────────────────────────────────────────────
function onData(d) {
  // Top bar
  const price = d.spot_price || 0;
  document.getElementById('spot').textContent = '$' + price.toFixed(2);
  if (prevPrice) {
    const chg = price - prevPrice;
    const el = document.getElementById('price-chg');
    el.textContent = (chg >= 0 ? '▲ +' : '▼ ') + chg.toFixed(2);
    el.className = 'badge ' + (chg >= 0 ? 'badge-green' : 'badge-red');
  }
  prevPrice = price;
  document.getElementById('ts').textContent = (d.ts||'').slice(11,19)+' UTC';
  document.getElementById('tick-ct').textContent = d.tick_count || 0;
  document.getElementById('learn-it').textContent = d.learner_iters || 0;

  // Bot status
  const statusEl = document.getElementById('bot-status');
  statusEl.textContent = d.is_active ? 'ACTIVE' : 'PAUSED';
  statusEl.className = 'badge ' + (d.is_active ? 'badge-green' : 'badge-red');
  document.getElementById('circuit-badge').style.display = d.circuit_breaker ? '' : 'none';

  // Stats
  const acc = d.account || {};
  const fmt = v => '$' + (v||0).toFixed(2);
  const fmtPnl = v => (v >= 0 ? '+' : '') + '$' + (v||0).toFixed(4);
  document.getElementById('balance').textContent = fmt(acc.balance);
  document.getElementById('equity').textContent  = fmt(acc.equity);
  const upnlEl = document.getElementById('upnl');
  upnlEl.textContent = fmtPnl(acc.unrealized_pnl);
  upnlEl.style.color = (acc.unrealized_pnl||0) >= 0 ? 'var(--green)' : 'var(--red)';
  const rpnlEl = document.getElementById('rpnl');
  rpnlEl.textContent = fmtPnl(acc.realized_pnl);
  rpnlEl.style.color = (acc.realized_pnl||0) >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('open-pos').textContent = acc.open_positions || 0;

  // Equity curve — append realized PnL point
  const now = Math.floor(Date.now()/1000);
  pnlHistory.push({ time: now, value: parseFloat((acc.realized_pnl||0).toFixed(4)) });
  // Deduplicate by time (chart requires unique ascending)
  const seen = {};
  pnlHistory = pnlHistory.filter(p => seen[p.time] ? false : (seen[p.time]=true));
  pnlSeries.setData(pnlHistory.slice(-500));
  pnlSeries.applyOptions({
    color: (acc.realized_pnl||0) >= 0 ? '#3fb950' : '#f85149'
  });

  // Autopilot gauge
  setGauge(d.ap_overall || 0);
  renderTargets(d.targets || []);
  renderDirective(d.ap_directive || {});
  renderWeights(d.learner_weights || {});
  renderMetrics(d.ap_metrics || {});

  // Tables
  renderPositions(d.positions || []);
  renderTrades(d.recent_trades || []);

  // Ticker bar
  const closed = (d.recent_trades||[]).filter(t=>t.status==='CLOSED');
  const wins   = closed.filter(t=>t.realized_pnl>0).length;
  const wr     = closed.length ? ((wins/closed.length)*100).toFixed(1)+'%' : 'N/A';
  document.getElementById('ticker-bar').innerHTML =
    `🟡 XAUUSD&nbsp; $${price.toFixed(2)}&nbsp;&nbsp;|&nbsp;&nbsp;` +
    `Closed Trades: ${closed.length}&nbsp;&nbsp;|&nbsp;&nbsp;` +
    `Win Rate: ${wr}&nbsp;&nbsp;|&nbsp;&nbsp;` +
    `Realized: ${fmtPnl(acc.realized_pnl)}&nbsp;&nbsp;|&nbsp;&nbsp;` +
    `Learner Iters: ${d.learner_iters||0}`;
}

// ── WebSocket connection ─────────────────────────────────────────────────────
function connect() {
  const ws = new WebSocket('ws://' + location.host + '/ws');
  ws.onmessage = e => { try { onData(JSON.parse(e.data)); } catch(err) { console.error(err); } };
  ws.onclose   = () => { setTimeout(connect, 2000); };
  ws.onerror   = () => ws.close();
  // Keep alive ping
  setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 25000);
}
connect();

// Resize chart on window resize
window.addEventListener('resize', () => {
  chart.applyOptions({ width: chartEl.clientWidth });
});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("dashboard_server:app", host=host, port=port,
                reload=False, log_level="info")
