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
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from autopilot import Autopilot
from knowledge_db import KnowledgeDatabase

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATE_FILE   = Path("logs/paper_state.json")
TUNE_FILE    = Path("logs/tune_directive.json")
PERF_FILE    = Path("logs/autopilot_targets.json")
PERF_LOG     = Path("logs/performance_log.json")

app = FastAPI(title="Gold AI Trader Dashboard")
autopilot = Autopilot()
db = KnowledgeDatabase()

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
        "ts":              datetime.now(timezone.utc).isoformat(),
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
        "knowledge_summary": db.get_knowledge_summary(),
        "poc_history":       db.get_poc_history(limit=10),
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
<title>Gold AI Trader — Autonomous Control Dashboard</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg:       #090d12;
    --card:     #12171f;
    --border:   #262f3d;
    --gold:     #f5b027;
    --green:    #22c55e;
    --red:      #ef4444;
    --blue:     #3b82f6;
    --purple:   #a855f7;
    --muted:    #64748b;
    --text:     #f8fafc;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'SF Mono', monospace; font-size: 12px; }

  /* ── Layout ── */
  .topbar { display: flex; align-items: center; justify-content: space-between;
            padding: 10px 18px; background: var(--card); border-bottom: 1px solid var(--border); }
  .topbar-left { display: flex; align-items: center; gap: 14px; }
  .logo { font-size: 15px; font-weight: 800; color: var(--gold); letter-spacing: 1px; display: flex; align-items: center; gap: 6px; }
  .price-badge { font-size: 20px; font-weight: 800; color: var(--gold); transition: color .2s ease; }
  .badge { padding: 3px 8px; border-radius: 10px; font-size: 10px; font-weight: 700; text-transform: uppercase; }
  .badge-green { background: rgba(34,197,94,.15); color: var(--green); border: 1px solid rgba(34,197,94,.3); }
  .badge-red   { background: rgba(239,68,68,.15);  color: var(--red);   border: 1px solid rgba(239,68,68,.3); }
  .badge-gold  { background: rgba(245,176,39,.15); color: var(--gold);  border: 1px solid rgba(245,176,39,.3); }

  .btn { background: #1e293b; color: var(--text); border: 1px solid var(--border); border-radius: 6px;
         padding: 5px 12px; font-family: inherit; font-size: 11px; font-weight: 600; cursor: pointer; transition: all .15s ease; }
  .btn:hover { background: #334155; border-color: var(--gold); }
  .btn-buy  { background: rgba(34,197,94,.2); border-color: var(--green); color: var(--green); }
  .btn-buy:hover { background: var(--green); color: #000; }
  .btn-sell { background: rgba(239,68,68,.2); border-color: var(--red); color: var(--red); }
  .btn-sell:hover { background: var(--red); color: #fff; }

  .main { display: grid; grid-template-columns: 1fr 360px; gap: 10px; padding: 10px; height: calc(100vh - 50px); overflow: hidden; }
  .col-left  { display: flex; flex-direction: column; gap: 10px; min-width: 0; overflow-y: auto; }
  .col-right { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }

  .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
  .card-title { font-size: 10px; font-weight: 800; color: var(--muted); text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; }

  /* ── Stat row ── */
  .stats-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
  .stat-label { font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 2px; }
  .stat-value { font-size: 18px; font-weight: 800; }

  /* ── Charts Grid ── */
  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .chart-box { height: 210px; width: 100%; }

  /* ── Targets & Gauges ── */
  .target-row { display: flex; align-items: center; gap: 6px; margin-bottom: 7px; }
  .target-label { width: 90px; font-size: 10px; color: var(--muted); }
  .target-bar-bg { flex: 1; height: 6px; background: #1e293b; border-radius: 3px; overflow: hidden; }
  .target-bar    { height: 100%; border-radius: 3px; transition: width .4s ease; }
  .target-vals   { width: 105px; text-align: right; font-size: 10px; }
  .target-status { width: 16px; font-size: 11px; }

  .gauge-wrap { display: flex; flex-direction: column; align-items: center; margin: 2px 0; }
  svg.gauge { width: 100px; height: 56px; }

  /* ── Learner weights ── */
  .weight-bar { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
  .weight-name { width: 65px; font-size: 9px; color: var(--muted); }
  .weight-fill { height: 7px; border-radius: 3px; background: var(--blue); transition: width .4s ease; min-width: 2px; }
  .weight-pct  { font-size: 9px; color: var(--text); width: 35px; text-align: right; }

  /* ── Directive panel ── */
  .directive-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .dir-item { background: #1e293b; border-radius: 6px; padding: 5px 8px; }
  .dir-key  { font-size: 9px; color: var(--muted); }
  .dir-val  { font-size: 13px; font-weight: 700; color: var(--gold); }

  /* ── Tables ── */
  .tbl { width: 100%; border-collapse: collapse; font-size: 10px; }
  .tbl th { color: var(--muted); font-weight: 700; text-align: left; padding: 4px 5px;
             border-bottom: 1px solid var(--border); text-transform: uppercase; font-size: 9px; }
  .tbl td { padding: 4px 5px; border-bottom: 1px solid rgba(38,47,61,.5); }
  .tbl tr:last-child td { border-bottom: none; }
  .buy  { color: var(--green); font-weight: 800; }
  .sell { color: var(--red);   font-weight: 800; }
  .pnl-pos { color: var(--green); font-weight: 700; }
  .pnl-neg { color: var(--red);   font-weight: 700; }

  /* ── Flash Animations ── */
  .flash-green { animation: flashG .5s ease; }
  .flash-red   { animation: flashR .5s ease; }
  @keyframes flashG { 0% { background: rgba(34,197,94,.3); } 100% { background: transparent; } }
  @keyframes flashR { 0% { background: rgba(239,68,68,.3); } 100% { background: transparent; } }

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
    <span id="bot-status" class="badge badge-green">AUTOPILOT LIVE</span>
    <span class="badge" id="circuit-badge" style="display:none">🔴 CIRCUIT BREAKER</span>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <button class="btn" id="btn-toggle" onclick="sendCommand('toggle_active')">⏸ Pause Bot</button>
    <button class="btn btn-buy" onclick="sendCommand('manual_trade','BUY')">+ Paper BUY</button>
    <button class="btn btn-sell" onclick="sendCommand('manual_trade','SELL')">+ Paper SELL</button>
    <button class="btn" onclick="sendCommand('force_eval')">🔄 Re-Eval Autopilot</button>
    <span style="color:var(--muted);font-size:10px;margin-left:6px">Ticks: <b id="tick-ct" style="color:var(--text)">0</b></span>
    <span style="color:var(--muted);font-size:10px">Learner: <b id="learn-it" style="color:var(--gold)">0</b></span>
    <span id="ts" style="color:var(--muted);font-size:10px"></span>
  </div>
</div>

<!-- ═══════════════════════ MAIN LAYOUT ════════════════════ -->
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

    <!-- Charts Grid (Price Candlestick + Equity Curve) -->
    <div class="charts-grid">
      <div class="card">
        <div class="card-title">🟡 Live Gold Candlesticks (XAUUSD) <span id="candlestick-tf" style="color:var(--gold)">1m Ticks</span></div>
        <div id="candle-chart" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-title">📈 Realized P&L Cumulative Curve <span style="color:var(--green)">Live Equity</span></div>
        <div id="equity-chart" class="chart-box"></div>
      </div>
    </div>

    <!-- Knowledge Base & Memory Stats -->
    <div class="card">
      <div class="card-title">🧠 SQLite Knowledge Base & Pattern Memory <span id="kb-trades-badge" class="badge badge-gold">0 Logged Setups</span></div>
      <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px">
        <div style="background:#1e293b;border-radius:6px;padding:6px 10px">
          <div style="font-size:9px;color:var(--muted)">TOTAL LOGGED TRADES</div>
          <div style="font-size:15px;font-weight:700;color:var(--gold)" id="kb-total">0</div>
        </div>
        <div style="background:#1e293b;border-radius:6px;padding:6px 10px">
          <div style="font-size:9px;color:var(--muted)">KNOWLEDGE WIN RATE</div>
          <div style="font-size:15px;font-weight:700;color:var(--green)" id="kb-winrate">0%</div>
        </div>
        <div style="background:#1e293b;border-radius:6px;padding:6px 10px">
          <div style="font-size:9px;color:var(--muted)">KNOWLEDGE CUMULATIVE P&L</div>
          <div style="font-size:15px;font-weight:700" id="kb-pnl">$0.00</div>
        </div>
        <div style="background:#1e293b;border-radius:6px;padding:6px 10px">
          <div style="font-size:9px;color:var(--muted)">POC AUDIT RECORDS</div>
          <div style="font-size:15px;font-weight:700;color:var(--blue)" id="kb-poc-cnt">0</div>
        </div>
      </div>
    </div>

    <!-- Open Positions Table -->
    <div class="card">
      <div class="card-title">🟢 Active Paper Positions</div>
      <table class="tbl">
        <thead><tr>
          <th>ID</th><th>Dir</th><th>Entry</th><th>Current</th>
          <th>Size</th><th>SL</th><th>TP</th><th>Unreal.PnL</th>
        </tr></thead>
        <tbody id="pos-body"><tr><td colspan="8" style="color:var(--muted);text-align:center;padding:8px">No active positions</td></tr></tbody>
      </table>
    </div>

    <!-- Trade Journal -->
    <div class="card">
      <div class="card-title">📋 Execution Trade Journal</div>
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

    <!-- Autopilot Score Gauge -->
    <div class="card">
      <div class="card-title">🤖 Autopilot Health Score</div>
      <div class="gauge-wrap">
        <svg class="gauge" viewBox="0 0 110 64">
          <path d="M 10 60 A 45 45 0 0 1 100 60" fill="none" stroke="#1e293b" stroke-width="10" stroke-linecap="round"/>
          <path id="gauge-arc" d="M 10 60 A 45 45 0 0 1 100 60" fill="none" stroke="var(--gold)" stroke-width="10"
                stroke-linecap="round" stroke-dasharray="141.4" stroke-dashoffset="141.4" style="transition:stroke-dashoffset .6s ease"/>
        </svg>
        <div style="font-size:24px;font-weight:800;color:var(--gold);margin-top:-22px" id="gauge-pct">0%</div>
        <div style="font-size:10px;color:var(--muted)" id="gauge-label">Evaluating targets…</div>
      </div>
    </div>

    <!-- Performance Targets -->
    <div class="card">
      <div class="card-title">🎯 Performance Targets</div>
      <div id="targets-list"></div>
    </div>

    <!-- Active Self-Tuning Directive -->
    <div class="card">
      <div class="card-title">⚙️ Active Tuning Directive</div>
      <div class="directive-grid" id="directive-grid">
        <div class="dir-item"><div class="dir-key">Risk %</div><div class="dir-val" id="d-risk">1.5%</div></div>
        <div class="dir-item"><div class="dir-key">SL Mult</div><div class="dir-val" id="d-sl">1.5x</div></div>
        <div class="dir-item"><div class="dir-key">TP Mult</div><div class="dir-val" id="d-tp">3.0x</div></div>
        <div class="dir-item"><div class="dir-key">Cooldown</div><div class="dir-val" id="d-cool">30s</div></div>
        <div class="dir-item"><div class="dir-key">Max Pos</div><div class="dir-val" id="d-maxpos">1</div></div>
        <div class="dir-item"><div class="dir-key">Updated</div><div class="dir-val" id="d-ts" style="font-size:9px;color:var(--muted)">—</div></div>
      </div>
    </div>

    <!-- Learner Model Weights -->
    <div class="card">
      <div class="card-title">🧠 Model Regret Weights</div>
      <div id="weights-list"></div>
      <div style="margin-top:6px;font-size:9px;color:var(--muted)">
        Learner Iterations: <b id="l-iters" style="color:var(--gold)">0</b>
      </div>
    </div>

    <!-- Proof-of-Concept Milestone History -->
    <div class="card">
      <div class="card-title">🏆 Proof of Concept Audit Log</div>
      <table class="tbl">
        <thead><tr>
          <th>Time</th><th>Trades</th><th>WinRate</th><th>Equity</th><th>Sharpe</th>
        </tr></thead>
        <tbody id="poc-body"><tr><td colspan="5" style="color:var(--muted);text-align:center;padding:6px">No milestone records yet</td></tr></tbody>
      </table>
    </div>

  </div><!-- /col-right -->

</div><!-- /main -->

<!-- ═══════════════════════ JAVASCRIPT ════════════════════ -->
<script>
// ── Chart initialization ───────────────────────────────────────────────────
const candleEl = document.getElementById('candle-chart');
const candleChart = LightweightCharts.createChart(candleEl, {
  width: candleEl.clientWidth, height: 200,
  layout: { background: { color: '#12171f' }, textColor: '#64748b' },
  grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  timeScale: { borderColor: '#262f3d', timeVisible: true },
  rightPriceScale: { borderColor: '#262f3d' },
});
const candleSeries = candleChart.addCandlestickSeries({
  upColor: '#22c55e', downColor: '#ef4444', borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444'
});

const equityEl = document.getElementById('equity-chart');
const equityChart = LightweightCharts.createChart(equityEl, {
  width: equityEl.clientWidth, height: 200,
  layout: { background: { color: '#12171f' }, textColor: '#64748b' },
  grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  timeScale: { borderColor: '#262f3d', timeVisible: true },
  rightPriceScale: { borderColor: '#262f3d' },
});
const equitySeries = equityChart.addLineSeries({
  color: '#22c55e', lineWidth: 2, priceLineVisible: false
});

let prevPrice = 0;
let pnlHistory = [{ time: Math.floor(Date.now()/1000), value: 0 }];
let candleHistory = [];
let currentCandle = null;

// ── Control Commands ────────────────────────────────────────────────────────
async function sendCommand(cmd, dir = null) {
  const payload = { command: cmd };
  if (dir) payload.direction = dir;
  try {
    const res = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    console.log('Command sent:', data);
  } catch(err) {
    console.error('Command failed:', err);
  }
}

// ── Gauge helper ────────────────────────────────────────────────────────────
function setGauge(pct) {
  const arc = document.getElementById('gauge-arc');
  const total = 141.4;
  arc.style.strokeDashoffset = total - (total * pct);
  arc.style.stroke = pct >= 0.75 ? '#22c55e' : pct >= 0.50 ? '#f5b027' : '#ef4444';
  document.getElementById('gauge-pct').textContent = Math.round(pct * 100) + '%';
  document.getElementById('gauge-label').textContent =
    pct >= 0.75 ? '✅ On Target' : pct >= 0.50 ? '⚠️ Self-Tuning' : '❌ Off Target';
}

// ── Render Helpers ──────────────────────────────────────────────────────────
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

function renderWeights(weights) {
  const el = document.getElementById('weights-list');
  el.innerHTML = '';
  const entries = Object.entries(weights).sort((a,b) => b[1]-a[1]);
  const colors = ['#3b82f6','#a855f7','#22c55e','#f5b027','#ef4444'];
  entries.forEach(([k,v], i) => {
    el.innerHTML += `<div class="weight-bar">
      <span class="weight-name">${k}</span>
      <div class="weight-fill" style="width:${Math.round(v*180)}px;background:${colors[i%colors.length]}"></div>
      <span class="weight-pct">${(v*100).toFixed(1)}%</span>
    </div>`;
  });
}

function renderDirective(d) {
  if (!d || !Object.keys(d).length) return;
  document.getElementById('d-risk').textContent = ((d.risk_pct||.015)*100).toFixed(1)+'%';
  document.getElementById('d-sl').textContent   = (d.sl_atr_mult||1.5)+'x';
  document.getElementById('d-tp').textContent   = (d.tp_atr_mult||3.0)+'x';
  document.getElementById('d-cool').textContent = (d.cooldown_sec||30)+'s';
  document.getElementById('d-maxpos').textContent= d.max_positions||1;
  document.getElementById('d-ts').textContent   = (d.updated_at||'').slice(11,16)+' UTC';
}

function renderPositions(positions) {
  const tb = document.getElementById('pos-body');
  if (!positions.length) {
    tb.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:8px">No active positions</td></tr>';
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

function renderTrades(trades) {
  const tb = document.getElementById('trades-body');
  const closed = trades.filter(t => t.status === 'CLOSED').slice(-10).reverse();
  if (!closed.length) {
    tb.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:8px">No closed trades yet</td></tr>';
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
      <td style="font-size:9px;color:var(--muted)">${t.exit_reason||''}</td>
      <td style="color:var(--muted)">${time}</td>
    </tr>`;
  }).join('');
}

function renderKnowledgeSummary(ks) {
  if (!ks) return;
  document.getElementById('kb-total').textContent = ks.total_trades_logged || 0;
  document.getElementById('kb-winrate').textContent = ((ks.knowledge_win_rate||0)*100).toFixed(1)+'%';
  const pnl = ks.total_knowledge_pnl || 0;
  const pnlEl = document.getElementById('kb-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
  pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('kb-poc-cnt').textContent = ks.poc_audit_records || 0;
  document.getElementById('kb-trades-badge').textContent = (ks.total_trades_logged || 0) + ' Logged Setups';
}

function renderPocHistory(poc) {
  const tb = document.getElementById('poc-body');
  if (!poc || !poc.length) {
    tb.innerHTML = '<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:6px">No milestone records yet</td></tr>';
    return;
  }
  tb.innerHTML = poc.slice(0, 5).map(r => `<tr>
    <td style="color:var(--muted)">${(r.timestamp||'').slice(11,16)}</td>
    <td>${r.total_trades}</td>
    <td style="color:var(--green)">${((r.win_rate||0)*100).toFixed(1)}%</td>
    <td style="font-weight:700">$${(r.equity||10000).toFixed(2)}</td>
    <td style="color:var(--gold)">${(r.sharpe_ratio||0).toFixed(2)}</td>
  </tr>`).join('');
}

// ── Main Data Handler ───────────────────────────────────────────────────────
function onData(d) {
  const price = d.spot_price || 0;
  const spotEl = document.getElementById('spot');
  spotEl.textContent = '$' + price.toFixed(2);

  if (prevPrice && price !== prevPrice) {
    const chg = price - prevPrice;
    const el = document.getElementById('price-chg');
    el.textContent = (chg >= 0 ? '▲ +' : '▼ ') + chg.toFixed(2);
    el.className = 'badge ' + (chg >= 0 ? 'badge-green' : 'badge-red');
    spotEl.classList.remove('flash-green', 'flash-red');
    void spotEl.offsetWidth; // trigger reflow
    spotEl.classList.add(chg >= 0 ? 'flash-green' : 'flash-red');
  }
  prevPrice = price;

  // Update Candlestick Chart
  const now = Math.floor(Date.now()/1000);
  const candleTime = now - (now % 60); // 1m bar
  if (!currentCandle || currentCandle.time !== candleTime) {
    currentCandle = { time: candleTime, open: price, high: price, low: price, close: price };
    candleHistory.push(currentCandle);
    if (candleHistory.length > 200) candleHistory.shift();
  } else {
    currentCandle.high = Math.max(currentCandle.high, price);
    currentCandle.low = Math.min(currentCandle.low, price);
    currentCandle.close = price;
  }
  candleSeries.update(currentCandle);

  document.getElementById('ts').textContent = (d.ts||'').slice(11,19)+' UTC';
  document.getElementById('tick-ct').textContent = d.tick_count || 0;
  document.getElementById('learn-it').textContent = d.learner_iters || 0;

  // Bot status & button
  const statusEl = document.getElementById('bot-status');
  const btnToggle = document.getElementById('btn-toggle');
  statusEl.textContent = d.is_active ? 'AUTOPILOT LIVE' : 'PAUSED';
  statusEl.className = 'badge ' + (d.is_active ? 'badge-green' : 'badge-red');
  btnToggle.textContent = d.is_active ? '⏸ Pause Bot' : '▶ Resume Bot';
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

  // Equity Curve Chart
  pnlHistory.push({ time: now, value: parseFloat((acc.realized_pnl||0).toFixed(4)) });
  const seen = {};
  pnlHistory = pnlHistory.filter(p => seen[p.time] ? false : (seen[p.time]=true));
  equitySeries.setData(pnlHistory.slice(-200));

  setGauge(d.ap_overall || 0);
  renderTargets(d.targets || []);
  renderDirective(d.ap_directive || {});
  renderWeights(d.learner_weights || {});
  renderPositions(d.positions || []);
  renderTrades(d.recent_trades || []);
  renderKnowledgeSummary(d.knowledge_summary);
  renderPocHistory(d.poc_history);
}

// ── Auto-Polling Fallback + WebSocket Connection ───────────────────────────
async function fetchState() {
  try {
    const res = await fetch('/api/state');
    if (res.ok) {
      const data = await res.json();
      onData(data);
    }
  } catch(e) {}
}

function connect() {
  const ws = new WebSocket('ws://' + location.host + '/ws');
  ws.onmessage = e => { try { onData(JSON.parse(e.data)); } catch(err) { console.error(err); } };
  ws.onclose   = () => { setTimeout(connect, 2000); };
  ws.onerror   = () => ws.close();
  setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 25000);
}

connect();
setInterval(fetchState, 1000); // 1-second continuous auto-polling fallback

window.addEventListener('resize', () => {
  candleChart.applyOptions({ width: candleEl.clientWidth });
  equityChart.applyOptions({ width: equityEl.clientWidth });
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
