"""
dashboard_server.py — FastAPI High-Frequency Institutional Trading Dashboard 2.0.
Reads logs/paper_state.json, tune directives, autopilot metrics, and SQLite knowledge base.
Pushes real-time updates via WebSocket to all connected browsers at http://127.0.0.1:8765.

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

from autopilot import Autopilot
from knowledge_db import KnowledgeDatabase

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

STATE_FILE   = Path("logs/paper_state.json")
TUNE_FILE    = Path("logs/tune_directive.json")
PERF_FILE    = Path("logs/autopilot_targets.json")
PERF_LOG     = Path("logs/performance_log.json")
CMD_FILE     = Path("data/command_queue.json")

app = FastAPI(title="Gold AI Trader Dashboard 2.0")
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
        "ts":                  datetime.now(timezone.utc).isoformat(),
        "spot_price":          state.get("spot_price", 0),
        "is_active":           state.get("is_active", True),
        "circuit_breaker":     state.get("circuit_breaker", False),
        "account":             account,
        "positions":           state.get("positions", []),
        "recent_trades":       trades[-25:],
        "learner_weights":     state.get("learner_weights", {}),
        "learner_iters":       state.get("learner_iterations", 0),
        "tick_count":          state.get("tick_count", 0),
        "ensemble_signals":    state.get("ensemble_signals", {}),
        "consensus_score":     state.get("consensus_score", 0.0),
        "consensus_direction": state.get("consensus_direction", "FLAT"),
        "consensus_confidence": state.get("consensus_confidence", 0.0),
        "uncertainty":         state.get("uncertainty", 0.0),
        "lead_regime":         state.get("lead_regime", "QUANT"),
        "cooldown_remaining":  state.get("cooldown_remaining", 0),
        "google_decision":     state.get("google_decision", {}),
        "factual_indicators":  state.get("factual_indicators", {}),
        "kb_intelligence":     state.get("kb_intelligence", {}),
        "targets":             autopilot.get_targets_display(metrics),
        "ap_metrics":          metrics,
        "ap_scores":           ap_snap.get("scores", {}),
        "ap_overall":          ap_snap.get("overall_score", 0),
        "ap_directive":        tune,
        "ap_status":           ap_snap.get("status", {}),
        "equity_curve":        equity_curve,
        "perf_history":        perf_h[-10:],
        "knowledge_summary":   db.get_knowledge_summary(),
        "poc_history":         db.get_poc_history(limit=10),
        "grid_state":          state.get("grid_state", {}),
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
        await asyncio.sleep(1.5)


@app.on_event("startup")
async def startup():
    Path("logs").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)
    asyncio.create_task(_broadcast_loop())
    logger.info("Dashboard 2.0 server started — http://127.0.0.1:8765")


# ── HTTP API Endpoints ────────────────────────────────────────────────────────
@app.get("/api/state")
async def api_state():
    return JSONResponse(_build_payload())


@app.get("/api/google_analyze")
async def api_google_analyze():
    """On-demand Google Gemini market reasoning with minimal token consumption."""
    from models.google_native_engine import GoogleNativeTradingEngine
    engine = GoogleNativeTradingEngine()
    state = _read_json(STATE_FILE)
    features = {
        "price": state.get("spot_price", 4427.0),
        "rsi": 42.0,
        "adx": 36.0,
        "ema_status": "BEAR" if state.get("consensus_direction") == "SELL" else "BULL",
        "roc": -0.85,
        "zscore": -0.4,
        "regime_lead": state.get("lead_regime", "QUANT")
    }
    engine._run_evaluation(features)
    return JSONResponse(engine.get_latest_signal())


@app.get("/api/targets")
async def api_targets():
    snap = _read_json(PERF_FILE)
    return JSONResponse({
        "targets":   autopilot.get_targets_display(snap.get("metrics")),
        "overall":   snap.get("overall_score", 0),
        "directive": _read_json(TUNE_FILE),
    })


@app.post("/api/command")
async def api_command(body: dict):
    CMD_FILE.parent.mkdir(exist_ok=True)
    existing = []
    if CMD_FILE.exists():
        try:
            existing = json.loads(CMD_FILE.read_text())
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:
            existing = []
    existing.append(body)
    CMD_FILE.write_text(json.dumps(existing))
    return {"status": "queued", "command": body}


@app.post("/api/tune")
async def api_tune(body: dict):
    """Save user tuning directive immediately."""
    directive = _read_json(TUNE_FILE)
    if not directive:
        directive = {
            "risk_pct": 0.015,
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 3.0,
            "cooldown_sec": 60,
            "max_positions": 1,
        }

    for k in ["risk_pct", "sl_atr_mult", "tp_atr_mult", "cooldown_sec", "max_positions"]:
        if k in body:
            directive[k] = float(body[k]) if "pct" in k or "mult" in k else int(body[k])

    directive["updated_at"] = datetime.now(timezone.utc).isoformat()
    TUNE_FILE.write_text(json.dumps(directive, indent=2))
    logger.info(f"[Dashboard API] Fine-tune directive updated: {directive}")
    return {"status": "success", "directive": directive}


@app.post("/api/close_all")
async def api_close_all():
    """Immediately queue a manual flatten command."""
    return await api_command({"command": "close_all"})




@app.post("/api/grid_control")
async def api_grid_control(body: dict):
    """Adjust grid mode (AUTO, BILATERAL, TREND_BIASED, OFF) or enabled status."""
    mode = body.get("mode", "AUTO")
    enabled = body.get("enabled", True)
    directive = _read_json(TUNE_FILE)
    directive["grid_mode"] = mode
    directive["grid_enabled"] = enabled
    directive["updated_at"] = datetime.now(timezone.utc).isoformat()
    TUNE_FILE.write_text(json.dumps(directive, indent=2))
    logger.info(f"[Grid API] Grid directive set: mode={mode}, enabled={enabled}")
    return {"status": "success", "grid_mode": mode, "grid_enabled": enabled}


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    logger.info(f"[WS] client connected  (total={len(clients)})")
    try:
        await ws.send_text(json.dumps(_build_payload()))
        while True:
            await ws.receive_text()
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
<title>GOLD AI TRADER 2.0 — Quantitative Hedge Terminal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg-base:      #080c14;
    --card-bg:      rgba(16, 22, 34, 0.85);
    --card-hover:   rgba(22, 30, 48, 0.95);
    --border:       rgba(255, 255, 255, 0.08);
    --border-glow:  rgba(245, 176, 39, 0.35);
    --gold:         #f5b027;
    --gold-glow:    rgba(245, 176, 39, 0.2);
    --green:        #10b981;
    --green-glow:   rgba(16, 185, 129, 0.2);
    --red:          #ef4444;
    --red-glow:     rgba(239, 68, 68, 0.2);
    --cyan:         #06b6d4;
    --purple:       #a855f7;
    --blue:         #3b82f6;
    --muted:        #64748b;
    --text:         #f1f5f9;
    --subtext:      #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: radial-gradient(circle at 50% 0%, #111a2e 0%, var(--bg-base) 70%);
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    height: 100vh;
    overflow: hidden;
  }

  /* ── Scrollbars ── */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
  ::-webkit-scrollbar-thumb:hover { background: #334155; }

  /* ── Topbar ── */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 18px;
    background: rgba(12, 17, 28, 0.9);
    backdrop-filter: blur(16px);
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 100;
  }
  .brand {
    display: flex; align-items: center; gap: 12px;
  }
  .brand-logo {
    font-size: 14px; font-weight: 800; color: var(--gold); letter-spacing: 1.5px;
    display: flex; align-items: center; gap: 6px;
    text-shadow: 0 0 16px var(--gold-glow);
  }
  .brand-tag {
    font-size: 9px; padding: 2px 6px; border-radius: 4px;
    background: rgba(245, 176, 39, 0.15); color: var(--gold);
    border: 1px solid rgba(245, 176, 39, 0.3); font-weight: 700;
  }
  .spot-container {
    display: flex; align-items: baseline; gap: 8px;
    background: rgba(0, 0, 0, 0.25); padding: 4px 12px; border-radius: 6px;
    border: 1px solid var(--border);
  }
  .spot-price {
    font-size: 22px; font-weight: 800; color: var(--gold);
    transition: all 0.2s ease;
  }
  .spot-chg {
    font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
  }

  /* ── Badges ── */
  .badge {
    padding: 3px 8px; border-radius: 4px; font-size: 9px; font-weight: 700;
    letter-spacing: 0.5px; text-transform: uppercase; display: inline-flex; align-items: center; gap: 4px;
  }
  .badge-green { background: rgba(16, 185, 129, 0.15); color: var(--green); border: 1px solid rgba(16, 185, 129, 0.3); }
  .badge-red   { background: rgba(239, 68, 68, 0.15);  color: var(--red);   border: 1px solid rgba(239, 68, 68, 0.3); }
  .badge-gold  { background: rgba(245, 176, 39, 0.15); color: var(--gold);  border: 1px solid rgba(245, 176, 39, 0.3); }
  .badge-cyan  { background: rgba(6, 182, 212, 0.15); color: var(--cyan);  border: 1px solid rgba(6, 182, 212, 0.3); }
  .pulse-dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--green);
    box-shadow: 0 0 10px var(--green); animation: pulse 1.6s infinite;
  }
  @keyframes pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.3); opacity: 0.4; } }

  /* ── Buttons ── */
  .btn-group { display: flex; align-items: center; gap: 6px; }
  .btn {
    background: #162032; color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 12px; font-family: inherit; font-size: 10px;
    font-weight: 700; cursor: pointer; transition: all 0.15s ease; display: inline-flex; align-items: center; gap: 5px;
  }
  .btn:hover { background: #223048; border-color: var(--gold); transform: translateY(-1px); }
  .btn-buy  { background: rgba(16, 185, 129, 0.18); border-color: var(--green); color: var(--green); }
  .btn-buy:hover { background: var(--green); color: #000; box-shadow: 0 0 12px var(--green-glow); }
  .btn-sell { background: rgba(239, 68, 68, 0.18); border-color: var(--red); color: var(--red); }
  .btn-sell:hover { background: var(--red); color: #fff; box-shadow: 0 0 12px var(--red-glow); }
  .btn-panic { background: rgba(239, 68, 68, 0.25); border: 1px solid var(--red); color: #fff; }
  .btn-panic:hover { background: #b91c1c; }
  .btn-apply { background: linear-gradient(135deg, #f5b027, #d97706); color: #000; border: none; font-weight: 800; }
  .btn-apply:hover { opacity: 0.95; box-shadow: 0 0 15px var(--gold-glow); }

  /* ── Layout ── */
  .main-layout {
    display: grid; grid-template-columns: 1fr 380px; gap: 8px; padding: 8px;
    height: calc(100vh - 46px); overflow: hidden;
  }
  .col-left { display: flex; flex-direction: column; gap: 8px; overflow-y: auto; min-width: 0; padding-right: 4px; }
  .col-right { display: flex; flex-direction: column; gap: 8px; overflow-y: auto; padding-right: 2px; }

  /* ── Cards ── */
  .card {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px; backdrop-filter: blur(12px); transition: border-color 0.2s ease;
  }
  .card:hover { border-color: rgba(255, 255, 255, 0.14); }
  .card-header {
    font-size: 10px; font-weight: 800; color: var(--subtext); text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between;
  }

  /* ── Stat Row ── */
  .stat-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
  .stat-card {
    background: rgba(14, 20, 32, 0.9); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 10px; position: relative; overflow: hidden;
  }
  .stat-card::after {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(245,176,39,0.3), transparent);
  }
  .stat-lbl { font-size: 8.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 2px; }
  .stat-val { font-size: 17px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }

  /* ── Charts Grid ── */
  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .chart-box { height: 200px; width: 100%; border-radius: 4px; overflow: hidden; }

  /* ── Quant Model Strategy Matrix ── */
  .matrix-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px; margin-top: 6px; }
  .matrix-card {
    background: rgba(11, 16, 26, 0.9); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px; display: flex; flex-direction: column; justify-content: space-between; min-height: 84px;
  }
  .m-name { font-size: 9px; font-weight: 800; color: var(--gold); display: flex; align-items: center; justify-content: space-between; }
  .m-meter { height: 5px; background: #162032; border-radius: 3px; overflow: hidden; margin: 6px 0; }
  .m-fill  { height: 100%; border-radius: 3px; transition: width 0.4s ease, background 0.3s ease; }
  .m-info  { font-size: 8.5px; color: var(--muted); line-height: 1.2; word-break: break-word; }

  /* ── Master Consensus Banner ── */
  .consensus-wrap {
    display: flex; align-items: center; justify-content: space-between;
    background: rgba(18, 25, 40, 0.95); border: 1px solid var(--border);
    padding: 8px 12px; border-radius: 6px; margin-bottom: 8px;
  }
  .consensus-meter-box { flex: 1; margin: 0 16px; }
  .consensus-bar-bg {
    height: 8px; background: #101622; border-radius: 4px; position: relative; overflow: hidden;
    border: 1px solid var(--border);
  }
  .consensus-center-mark { position: absolute; left: 50%; top: 0; bottom: 0; width: 2px; background: #475569; z-index: 2; }
  .consensus-indicator { position: absolute; top: 0; bottom: 0; border-radius: 3px; transition: all 0.3s ease; }

  /* ── Live Fine-Tuner Panel ── */
  .tuner-presets { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; margin-bottom: 10px; }
  .preset-btn {
    background: #141c2c; border: 1px solid var(--border); border-radius: 5px;
    padding: 5px; text-align: center; cursor: pointer; transition: all 0.15s ease;
  }
  .preset-btn:hover, .preset-btn.active { background: #1e2a42; border-color: var(--gold); }
  .p-title { font-size: 9px; font-weight: 800; color: var(--gold); }
  .p-desc { font-size: 7.5px; color: var(--muted); margin-top: 1px; }

  .slider-row { margin-bottom: 8px; }
  .slider-label-row { display: flex; justify-content: space-between; font-size: 9.5px; margin-bottom: 3px; }
  .slider-name { color: var(--subtext); }
  .slider-val  { font-weight: 800; color: var(--gold); }
  input[type="range"] {
    -webkit-appearance: none; width: 100%; height: 4px; border-radius: 2px;
    background: #1e293b; outline: none; margin: 2px 0;
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 12px; height: 12px; border-radius: 50%;
    background: var(--gold); cursor: pointer; box-shadow: 0 0 6px var(--gold);
  }

  /* ── Targets & Gauges ── */
  .target-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
  .target-label { width: 85px; font-size: 9.5px; color: var(--subtext); }
  .target-bar-bg { flex: 1; height: 5px; background: #162032; border-radius: 3px; overflow: hidden; }
  .target-bar    { height: 100%; border-radius: 3px; transition: width 0.4s ease; }
  .target-vals   { width: 100px; text-align: right; font-size: 9.5px; font-weight: 600; }
  .target-status { width: 16px; font-size: 10px; }

  /* ── Tables ── */
  .tbl { width: 100%; border-collapse: collapse; font-size: 10px; }
  .tbl th {
    color: var(--muted); font-weight: 700; text-align: left; padding: 4px 6px;
    border-bottom: 1px solid var(--border); text-transform: uppercase; font-size: 8.5px;
  }
  .tbl td { padding: 4px 6px; border-bottom: 1px solid rgba(255,255,255,0.03); }
  .tbl tr:hover td { background: rgba(255,255,255,0.02); }
  .buy  { color: var(--green); font-weight: 800; }
  .sell { color: var(--red);   font-weight: 800; }
  .pnl-pos { color: var(--green); font-weight: 800; }
  .pnl-neg { color: var(--red);   font-weight: 800; }

  /* ── Toasts ── */
  #toast-container {
    position: fixed; bottom: 16px; right: 16px; z-index: 1000;
    display: flex; flex-direction: column; gap: 8px; pointer-events: none;
  }
  .toast {
    background: rgba(16, 23, 38, 0.95); border: 1px solid var(--border);
    border-left: 4px solid var(--gold); border-radius: 6px; padding: 8px 12px;
    color: var(--text); font-size: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    backdrop-filter: blur(8px); animation: slideIn 0.3s ease forwards;
  }
  @keyframes slideIn { from { transform: translateX(50px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

  .flash-green { animation: flashG .4s ease; }
  .flash-red   { animation: flashR .4s ease; }
  @keyframes flashG { 0% { background: rgba(16,185,129,0.4); } 100% { background: transparent; } }
  @keyframes flashR { 0% { background: rgba(239,68,68,0.4); } 100% { background: transparent; } }
</style>
</head>
<body>

<!-- ═══════════════════════ TOPBAR ═══════════════════════ -->
<div class="topbar">
  <div class="brand">
    <span class="brand-logo">⚡ GOLD AI TRADER <span class="brand-tag">QUANT 2.0</span></span>
    <div class="spot-container">
      <span class="spot-price" id="spot">$-.--</span>
      <span id="price-chg" class="spot-chg badge-gold">--</span>
    </div>
    <span class="pulse-dot"></span>
    <span id="bot-status" class="badge badge-green">SYSTEM LIVE</span>
    <span id="cooldown-badge" class="badge badge-cyan" style="display:none">⏳ COOLDOWN</span>
    <span id="circuit-badge" class="badge badge-red" style="display:none">🔴 CIRCUIT BREAKER</span>
  </div>

  <div class="btn-group">
    <button class="btn" style="background:#131c2e;border-color:rgba(66,133,244,0.4);color:#60a5fa;font-weight:700" onclick="consultGoogleAI()">✨ Ask Gemini AI</button>
    <button class="btn" id="btn-toggle" onclick="sendCommand('toggle_active')">⏸ Pause Bot</button>
    <button class="btn btn-buy" onclick="sendCommand('manual_trade','BUY')">+ Paper BUY</button>
    <button class="btn btn-sell" onclick="sendCommand('manual_trade','SELL')">+ Paper SELL</button>
    <button class="btn btn-panic" onclick="closeAllPositions()">🚨 Flatten All</button>
    <button class="btn" onclick="sendCommand('force_eval')">🔄 Re-Eval</button>
    <span class="badge" style="background:rgba(66,133,244,0.12);border:1px solid rgba(66,133,244,0.35);color:#93c5fd;font-size:9px">
      🌐 Google AI: Gemini 3.6 Flash
    </span>
    <span style="color:var(--muted);font-size:9.5px;margin-left:6px">Ticks: <b id="tick-ct" style="color:var(--text)">0</b></span>
    <span id="ts" style="color:var(--muted);font-size:9.5px"></span>
  </div>
</div>

<!-- ═══════════════════════ MAIN LAYOUT ════════════════════ -->
<div class="main-layout">

  <!-- LEFT COLUMN: Operations & Charts -->
  <div class="col-left">

    <!-- Top Key Metric Cards -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-lbl">Account Balance</div>
        <div class="stat-val" id="balance">$--</div>
      </div>
      <div class="stat-card">
        <div class="stat-lbl">Live Net Equity</div>
        <div class="stat-val" id="equity" style="color:var(--gold)">$--</div>
      </div>
      <div class="stat-card">
        <div class="stat-lbl">Unrealized P&L</div>
        <div class="stat-val" id="upnl">--</div>
      </div>
      <div class="stat-card">
        <div class="stat-lbl">Realized Net P&L</div>
        <div class="stat-val" id="rpnl">--</div>
      </div>
      <div class="stat-card">
        <div class="stat-lbl">Active Positions</div>
        <div class="stat-val" id="open-pos" style="color:var(--cyan)">0</div>
      </div>
    </div>

    <!-- Master Quant Consensus & Signal Radar -->
    <div class="card">
      <div class="card-header">
        <span>🎯 Master Multi-Strategy Quantitative Consensus</span>
        <span id="lead-regime-badge" class="badge badge-gold">QUANT ENSEMBLE</span>
      </div>

      <!-- Factual Technical & Microstructure HUD -->
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:8px">
        <div style="background:#111927;border:1px solid var(--border);border-radius:6px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">14-PERIOD ATR</div>
          <div style="font-size:13px;font-weight:800;color:var(--gold)" id="hud-atr">$--</div>
        </div>
        <div style="background:#111927;border:1px solid var(--border);border-radius:6px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">14-PERIOD RSI</div>
          <div style="font-size:13px;font-weight:800;color:var(--cyan)" id="hud-rsi">--</div>
        </div>
        <div style="background:#111927;border:1px solid var(--border);border-radius:6px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">BID/ASK SPREAD</div>
          <div style="font-size:13px;font-weight:800;color:var(--green)">$0.15</div>
        </div>
        <div style="background:#111927;border:1px solid var(--border);border-radius:6px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">PROFIT FACTOR (SQLITE)</div>
          <div style="font-size:13px;font-weight:800;color:var(--gold)" id="hud-pf">1.21</div>
        </div>
        <div style="background:#111927;border:1px solid var(--border);border-radius:6px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">TRADE VELOCITY</div>
          <div style="font-size:13px;font-weight:800;color:#c084fc">FAST (3.0s)</div>
        </div>
      </div>

      <!-- Knowledge Pattern Intelligence Banner -->
      <div style="background:rgba(245,176,39,0.08);border:1px solid rgba(245,176,39,0.25);border-radius:6px;padding:6px 10px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between">
        <span style="font-size:9.5px;color:var(--gold);font-weight:700" id="kb-intel-text">📚 Knowledge Memory: Active across 468+ historical setups</span>
        <span class="badge badge-gold" id="kb-intel-badge">1.00x Sizing</span>
      </div>

      <div class="consensus-wrap">
        <div>
          <div style="font-size:8.5px;color:var(--muted)">CONSENSUS SIGNAL</div>
          <div style="font-size:16px;font-weight:800;" id="consensus-dir">FLAT</div>
        </div>

        <div class="consensus-meter-box">
          <div style="display:flex;justify-content:space-between;font-size:8px;color:var(--muted);margin-bottom:2px">
            <span>BEARISH (-1.0)</span>
            <span id="consensus-score-txt">SCORE: 0.00</span>
            <span>BULLISH (+1.0)</span>
          </div>
          <div class="consensus-bar-bg">
            <div class="consensus-center-mark"></div>
            <div id="consensus-indicator" class="consensus-indicator"></div>
          </div>
        </div>

        <div style="text-align:right">
          <div style="font-size:8.5px;color:var(--muted)">DISAGREEMENT / UNCERTAINTY</div>
          <div style="font-size:14px;font-weight:800;color:var(--subtext)" id="uncertainty-idx">0.00</div>
        </div>
      </div>

      <!-- 5 Model Strategy Matrix -->
      <div class="matrix-grid">
        <div class="matrix-card">
          <div class="m-name"><span>📈 TREND</span> <span id="s-trend-val">--</span></div>
          <div class="m-meter"><div class="m-fill" id="s-trend-bar"></div></div>
          <div class="m-info" id="s-trend-desc">EMA 12/26/50 + ADX</div>
        </div>
        <div class="matrix-card">
          <div class="m-name"><span>🌊 MEAN REV</span> <span id="s-mean-val">--</span></div>
          <div class="m-meter"><div class="m-fill" id="s-mean-bar"></div></div>
          <div class="m-info" id="s-mean-desc">RSI 14 + Bollinger</div>
        </div>
        <div class="matrix-card">
          <div class="m-name"><span>⚡ MOMENTUM</span> <span id="s-mom-val">--</span></div>
          <div class="m-meter"><div class="m-fill" id="s-mom-bar"></div></div>
          <div class="m-info" id="s-mom-desc">MACD Hist + ROC</div>
        </div>
        <div class="matrix-card">
          <div class="m-name"><span>🌐 MACRO</span> <span id="s-macro-val">--</span></div>
          <div class="m-meter"><div class="m-fill" id="s-macro-bar"></div></div>
          <div class="m-info" id="s-macro-desc">DXY Corr + Yield</div>
        </div>
        <div class="matrix-card">
          <div class="m-name"><span>🤖 ML POLICY</span> <span id="s-ml-val">--</span></div>
          <div class="m-meter"><div class="m-fill" id="s-ml-bar"></div></div>
          <div class="m-info" id="s-ml-desc">HistGradientBoosting</div>
        </div>
        <div class="matrix-card" style="border-color:rgba(66,133,244,0.35);background:rgba(15,23,42,0.95)">
          <div class="m-name" style="color:#60a5fa"><span>✨ GOOGLE GEMINI</span> <span id="s-google-val">--</span></div>
          <div class="m-meter"><div class="m-fill" id="s-google-bar"></div></div>
          <div class="m-info" id="s-google-desc">Gemini 3.6 Flash Native</div>
        </div>
      </div>
    </div>

    <!-- Charts Grid (Candlesticks & Live Equity) -->
    <div class="charts-grid">
      <div class="card">
        <div class="card-header">
          <span>🟡 Live Gold Candlesticks (XAUUSD)</span>
          <span style="color:var(--gold);font-size:9px">1-Min Aggregate</span>
        </div>
        <div id="candle-chart" class="chart-box"></div>
      </div>
      <div class="card">
        <div class="card-header">
          <span>📈 Realized Cumulative P&L Curve</span>
          <span style="color:var(--green);font-size:9px">Equity Trajectory</span>
        </div>
        <div id="equity-chart" class="chart-box"></div>
      </div>
    </div>

    <!-- Active Positions Table -->
    <div class="card">
      <div class="card-header">
        <span>🟢 Active Positions & Breakeven Locks</span>
        <span id="pos-count-badge" class="badge badge-cyan">0 Active</span>
      </div>
      <table class="tbl">
        <thead><tr>
          <th>ID</th><th>Dir</th><th>Entry</th><th>Current</th>
          <th>Size</th><th>SL (Trail)</th><th>TP</th><th>Unreal. P&L</th>
        </tr></thead>
        <tbody id="pos-body">
          <tr><td colspan="8" style="color:var(--muted);text-align:center;padding:8px">No active positions — observing market consensus</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Execution Trade Journal -->
    <div class="card">
      <div class="card-header">
        <span>📋 Closed Trade Execution Journal</span>
        <span style="color:var(--subtext);font-size:9px">Last 20 Executions</span>
      </div>
      <table class="tbl">
        <thead><tr>
          <th>ID</th><th>Dir</th><th>Entry</th><th>Exit</th>
          <th>Size</th><th>Net P&L</th><th>Exit Reason</th><th>Time</th>
        </tr></thead>
        <tbody id="trades-body"></tbody>
      </table>
    </div>

  </div><!-- /col-left -->

  <!-- RIGHT COLUMN: Control, Fine-Tuning & Knowledge Base -->
  <div class="col-right">

    <!-- 🎛️ Live Fine-Tuner Suite -->
    <div class="card" style="border-color:var(--border-glow)">
      <div class="card-header">
        <span style="color:var(--gold)">🎛️ Interactive Fine-Tuning Engine</span>
        <span class="badge badge-gold" id="tuning-status-badge">DIRECTIVE ACTIVE</span>
      </div>

      <!-- Presets -->
      <div class="tuner-presets">
        <div class="preset-btn" onclick="applyPreset('conservative')">
          <div class="p-title">🛡️ Safe</div>
          <div class="p-desc">0.8% | 2.0x SL</div>
        </div>
        <div class="preset-btn active" onclick="applyPreset('balanced')">
          <div class="p-title">⚖️ Balanced</div>
          <div class="p-desc">1.5% | 1.5x SL</div>
        </div>
        <div class="preset-btn" onclick="applyPreset('sniper')">
          <div class="p-title">🎯 Sniper</div>
          <div class="p-desc">1.2% | 4.0x TP</div>
        </div>
        <div class="preset-btn" onclick="applyPreset('aggressive')">
          <div class="p-title">🚀 Scalp</div>
          <div class="p-desc">2.2% | 20s Cool</div>
        </div>
      </div>

      <!-- Sliders -->
      <div class="slider-row">
        <div class="slider-label-row">
          <span class="slider-name">Risk Per Trade:</span>
          <span class="slider-val" id="val-risk">1.5%</span>
        </div>
        <input type="range" id="sl-risk" min="0.5" max="4.0" step="0.1" value="1.5" oninput="onSliderChange('risk', this.value, '%')">
      </div>

      <div class="slider-row">
        <div class="slider-label-row">
          <span class="slider-name">Stop-Loss ATR Multiplier:</span>
          <span class="slider-val" id="val-sl">1.5x</span>
        </div>
        <input type="range" id="sl-sl" min="0.8" max="3.5" step="0.1" value="1.5" oninput="onSliderChange('sl', this.value, 'x')">
      </div>

      <div class="slider-row">
        <div class="slider-label-row">
          <span class="slider-name">Take-Profit ATR Multiplier:</span>
          <span class="slider-val" id="val-tp">3.0x</span>
        </div>
        <input type="range" id="sl-tp" min="1.5" max="6.0" step="0.1" value="3.0" oninput="onSliderChange('tp', this.value, 'x')">
      </div>

      <div class="slider-row">
        <div class="slider-label-row">
          <span class="slider-name">Trade Cooldown Seconds:</span>
          <span class="slider-val" id="val-cool">60s</span>
        </div>
        <input type="range" id="sl-cool" min="10" max="300" step="5" value="60" oninput="onSliderChange('cool', this.value, 's')">
      </div>

      <div class="slider-row">
        <div class="slider-label-row">
          <span class="slider-name">Max Concurrent Positions:</span>
          <span class="slider-val" id="val-maxpos">1</span>
        </div>
        <input type="range" id="sl-maxpos" min="1" max="4" step="1" value="1" oninput="onSliderChange('maxpos', this.value, '')">
      </div>

      <button class="btn btn-apply" style="width:100%;justify-content:center;margin-top:6px" onclick="saveTuningDirective()">
        ⚡ Apply & Save Live Directive
      </button>
    </div>

    <!-- Autopilot Health Score Gauge -->
    <div class="card">
      <div class="card-header">
        <span>🤖 Autopilot Compliance Gauge</span>
        <span id="ap-status-txt" class="badge badge-green">EVALUATING</span>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-around;padding:4px 0">
        <div style="position:relative;width:90px;height:55px;display:flex;justify-content:center">
          <svg viewBox="0 0 110 64" style="width:90px;height:55px">
            <path d="M 10 60 A 45 45 0 0 1 100 60" fill="none" stroke="#162032" stroke-width="10" stroke-linecap="round"/>
            <path id="gauge-arc" d="M 10 60 A 45 45 0 0 1 100 60" fill="none" stroke="var(--gold)" stroke-width="10"
                  stroke-linecap="round" stroke-dasharray="141.4" stroke-dashoffset="141.4" style="transition:stroke-dashoffset .6s ease"/>
          </svg>
          <div style="position:absolute;bottom:0;font-size:18px;font-weight:800;color:var(--gold)" id="gauge-pct">0%</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:3px;font-size:9.5px">
          <div>Autopilot Cycle: <b id="ap-eval-cnt" style="color:var(--cyan)">#0</b></div>
          <div>Learner Updates: <b id="l-iters" style="color:var(--gold)">0</b></div>
          <div style="color:var(--muted);font-size:8.5px" id="ap-last-sync">--</div>
        </div>
      </div>
    </div>

    <!-- Performance Targets -->
    <div class="card">
      <div class="card-header">
        <span>🎯 Institutional Performance Targets</span>
        <span style="font-size:8.5px;color:var(--muted)">Self-Governing</span>
      </div>
      <div id="targets-list"></div>
    </div>

    <!-- Learner Weights -->
    <div class="card">
      <div class="card-header">
        <span>🧠 Dynamic Model Regret Weights</span>
        <span style="font-size:8.5px;color:var(--muted)">Closed-Loop</span>
      </div>
      <div id="weights-list"></div>
    </div>

    <!-- SQLite Knowledge Base & POC Milestones -->
    <div class="card">
      <div class="card-header">
        <span>🏆 SQLite Knowledge & Milestone Audits</span>
        <span id="kb-trades-badge" class="badge badge-gold">0 Setups</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:8px">
        <div style="background:#131a29;border-radius:5px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">KNOWLEDGE WIN RATE</div>
          <div style="font-size:14px;font-weight:800;color:var(--green)" id="kb-winrate">0.0%</div>
        </div>
        <div style="background:#131a29;border-radius:5px;padding:6px 8px">
          <div style="font-size:8px;color:var(--muted)">CUMULATIVE HISTORICAL P&L</div>
          <div style="font-size:14px;font-weight:800" id="kb-pnl">$0.00</div>
        </div>
      </div>
      <table class="tbl">
        <thead><tr><th>Time</th><th>Trades</th><th>Win%</th><th>Equity</th><th>Sharpe</th></tr></thead>
        <tbody id="poc-body">
          <tr><td colspan="5" style="color:var(--muted);text-align:center;padding:6px">No milestone records yet</td></tr>
        </tbody>
      </table>
    </div>

  </div><!-- /col-right -->

</div><!-- /main-layout -->

<!-- Toast Notification Container -->
<div id="toast-container"></div>

<!-- ═══════════════════════ JAVASCRIPT ════════════════════ -->
<script>
// ── Charts Setup ─────────────────────────────────────────────────────────────
const candleEl = document.getElementById('candle-chart');
const candleChart = LightweightCharts.createChart(candleEl, {
  width: candleEl.clientWidth, height: 200,
  layout: { background: { color: '#0c111c' }, textColor: '#64748b' },
  grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true },
  rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
});
const candleSeries = candleChart.addCandlestickSeries({
  upColor: '#10b981', downColor: '#ef4444', borderVisible: false,
  wickUpColor: '#10b981', wickDownColor: '#ef4444'
});

const equityEl = document.getElementById('equity-chart');
const equityChart = LightweightCharts.createChart(equityEl, {
  width: equityEl.clientWidth, height: 200,
  layout: { background: { color: '#0c111c' }, textColor: '#64748b' },
  grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  timeScale: { borderColor: 'rgba(255,255,255,0.08)', timeVisible: true },
  rightPriceScale: { borderColor: 'rgba(255,255,255,0.08)' },
});
const equitySeries = equityChart.addAreaSeries({
  topColor: 'rgba(16, 185, 129, 0.4)', bottomColor: 'rgba(16, 185, 129, 0.0)',
  lineColor: '#10b981', lineWidth: 2, priceLineVisible: false
});

let prevPrice = 0;
let pnlHistory = [{ time: Math.floor(Date.now()/1000), value: 0 }];
let candleHistory = [];
let currentCandle = null;
let lastKnownTradeCount = -1;

// ── Toasts ───────────────────────────────────────────────────────────────────
function showToast(msg, type = 'info') {
  const c = document.getElementById('toast-container');
  const t = document.createElement('div');
  t.className = 'toast';
  if (type === 'success') t.style.borderLeftColor = 'var(--green)';
  if (type === 'error')   t.style.borderLeftColor = 'var(--red)';
  t.innerHTML = msg;
  c.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 400); }, 3500);
}

// ── Commands & Control ───────────────────────────────────────────────────────
async function sendCommand(cmd, dir = null) {
  const payload = { command: cmd };
  if (dir) payload.direction = dir;
  try {
    const res = await fetch('/api/command', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    showToast(`Command Queued: <b>${cmd}</b>`, 'info');
  } catch(err) {
    showToast(`Command Error: ${err}`, 'error');
  }
}

async function closeAllPositions() {
  if (!confirm('Flatten and close ALL open positions immediately?')) return;
  try {
    await fetch('/api/close_all', { method: 'POST' });
    showToast('🚨 <b>FLATTEN ALL</b> triggered!', 'error');
  } catch(err) {
    showToast('Flatten error', 'error');
  }
}

async function consultGoogleAI() {
  showToast('🤖 Consulting Google Gemini 3.6 Flash...', 'info');
  try {
    const res = await fetch('/api/google_analyze');
    const data = await res.json();
    const action = data.action || 'HOLD';
    const conf = Math.round((data.confidence || 0) * 100);
    const tokens = data.tokens_used || 0;
    const latency = data.latency_ms || 0;
    const msg = `<b>Google Gemini Verdict: ${action} (${conf}% Conf)</b><br>${data.rationale}<br><small style="color:var(--muted)">Model: ${data.model} | Tokens: ${tokens} | Latency: ${latency}ms</small>`;
    showToast(msg, action === 'BUY' ? 'success' : action === 'SELL' ? 'error' : 'info');
  } catch(e) {
    showToast('Google Gemini consultation failed: ' + e, 'error');
  }
}

// ── Fine-Tuning Slider System ────────────────────────────────────────────────
function onSliderChange(field, val, unit) {
  document.getElementById(`val-${field}`).textContent = val + unit;
}

const PRESETS = {
  conservative: { risk_pct: 0.010, sl_atr_mult: 1.5, tp_atr_mult: 2.5, cooldown_sec: 30, max_positions: 1 },
  balanced:     { risk_pct: 0.015, sl_atr_mult: 1.2, tp_atr_mult: 2.0, cooldown_sec: 20, max_positions: 2 },
  sniper:       { risk_pct: 0.020, sl_atr_mult: 1.0, tp_atr_mult: 1.5, cooldown_sec: 15, max_positions: 2 },
  aggressive:   { risk_pct: 0.025, sl_atr_mult: 0.8, tp_atr_mult: 1.2, cooldown_sec: 10, max_positions: 3 },
};

function applyPreset(name) {
  document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('active'));
  event.currentTarget.classList.add('active');
  const p = PRESETS[name];
  if (!p) return;
  document.getElementById('sl-risk').value = (p.risk_pct * 100).toFixed(1);
  document.getElementById('val-risk').textContent = (p.risk_pct * 100).toFixed(1) + '%';
  document.getElementById('sl-sl').value = p.sl_atr_mult;
  document.getElementById('val-sl').textContent = p.sl_atr_mult + 'x';
  document.getElementById('sl-tp').value = p.tp_atr_mult;
  document.getElementById('val-tp').textContent = p.tp_atr_mult + 'x';
  document.getElementById('sl-cool').value = p.cooldown_sec;
  document.getElementById('val-cool').textContent = p.cooldown_sec + 's';
  document.getElementById('sl-maxpos').value = p.max_positions;
  document.getElementById('val-maxpos').textContent = p.max_positions;
  saveTuningDirective();
}

async function saveTuningDirective() {
  const payload = {
    risk_pct: parseFloat(document.getElementById('sl-risk').value) / 100.0,
    sl_atr_mult: parseFloat(document.getElementById('sl-sl').value),
    tp_atr_mult: parseFloat(document.getElementById('sl-tp').value),
    cooldown_sec: parseInt(document.getElementById('sl-cool').value),
    max_positions: parseInt(document.getElementById('sl-maxpos').value),
  };
  try {
    const res = await fetch('/api/tune', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      showToast('✅ <b>Parameters Applied Live</b> to Bot', 'success');
    }
  } catch(e) {
    showToast('Tuning save failed', 'error');
  }
}

// ── Render Helpers ──────────────────────────────────────────────────────────
function setGauge(pct) {
  const arc = document.getElementById('gauge-arc');
  const total = 141.4;
  arc.style.strokeDashoffset = total - (total * pct);
  arc.style.stroke = pct >= 0.75 ? '#10b981' : pct >= 0.50 ? '#f5b027' : '#ef4444';
  document.getElementById('gauge-pct').textContent = Math.round(pct * 100) + '%';
  document.getElementById('ap-status-txt').textContent =
    pct >= 0.75 ? 'ON TARGET' : pct >= 0.50 ? 'SELF-TUNING' : 'OFF TARGET';
  document.getElementById('ap-status-txt').className =
    'badge ' + (pct >= 0.75 ? 'badge-green' : pct >= 0.50 ? 'badge-gold' : 'badge-red');
}

function renderSignals(d) {
  const ens = d.ensemble_signals || {};
  const consensus = d.consensus_score || 0.0;
  const dir = d.consensus_direction || 'FLAT';
  const conf = d.consensus_confidence || 0.0;
  const uncert = d.uncertainty || 0.0;

  // Consensus indicator bar: -1.0 to +1.0
  const ind = document.getElementById('consensus-indicator');
  const scoreTxt = document.getElementById('consensus-score-txt');
  const dirEl = document.getElementById('consensus-dir');
  scoreTxt.textContent = `SCORE: ${(consensus > 0 ? '+' : '')}${consensus.toFixed(2)} (${Math.round(conf * 100)}% conf)`;
  dirEl.textContent = dir;
  dirEl.style.color = dir === 'BUY' ? 'var(--green)' : dir === 'SELL' ? 'var(--red)' : 'var(--text)';

  // Width & position from center
  const center = 50;
  const halfWidth = (consensus / 2.0) * 100;
  if (consensus >= 0) {
    ind.style.left = center + '%';
    ind.style.width = Math.min(50, halfWidth) + '%';
    ind.style.background = 'var(--green)';
  } else {
    ind.style.left = (center + halfWidth) + '%';
    ind.style.width = Math.min(50, Math.abs(halfWidth)) + '%';
    ind.style.background = 'var(--red)';
  }

  document.getElementById('uncertainty-idx').textContent = uncert.toFixed(2);
  document.getElementById('lead-regime-badge').textContent = (d.lead_regime || 'QUANT') + ' REGIME';

  // 6 Strategy Cards
  const models = [
    { key: 'trend', id: 'trend' },
    { key: 'mean_rev', id: 'mean' },
    { key: 'momentum', id: 'mom' },
    { key: 'macro', id: 'macro' },
    { key: 'ppo', id: 'ml' },
    { key: 'google_ai', id: 'google' },
  ];
  models.forEach(m => {
    const s = ens[m.key];
    const valEl = document.getElementById(`s-${m.id}-val`);
    const barEl = document.getElementById(`s-${m.id}-bar`);
    const descEl = document.getElementById(`s-${m.id}-desc`);
    if (s) {
      const sig = s.signal || 0;
      const c = s.confidence || 0;
      const sigTxt = sig > 0 ? `+${(c*100).toFixed(0)}% BULL` : sig < 0 ? `-${(c*100).toFixed(0)}% BEAR` : 'NEUTRAL';
      valEl.textContent = sigTxt;
      valEl.style.color = sig > 0 ? 'var(--green)' : sig < 0 ? 'var(--red)' : 'var(--muted)';
      barEl.style.width = Math.round(c * 100) + '%';
      barEl.style.background = sig > 0 ? 'var(--green)' : sig < 0 ? 'var(--red)' : 'var(--muted)';
      if (s.rationale) descEl.textContent = s.rationale;
    }
  });
}

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
  const colors = ['#f5b027','#10b981','#06b6d4','#3b82f6','#a855f7'];
  entries.forEach(([k,v], i) => {
    el.innerHTML += `
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
        <span style="width:65px;font-size:9px;color:var(--muted)">${k.toUpperCase()}</span>
        <div style="flex:1;height:5px;background:#162032;border-radius:3px;overflow:hidden">
          <div style="width:${Math.round(v*100)}%;height:100%;background:${colors[i%colors.length]};border-radius:3px"></div>
        </div>
        <span style="font-size:9px;color:var(--text);width:35px;text-align:right">${(v*100).toFixed(1)}%</span>
      </div>`;
  });
}

function renderPositions(positions) {
  const tb = document.getElementById('pos-body');
  document.getElementById('pos-count-badge').textContent = positions.length + ' Active';
  if (!positions.length) {
    tb.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:8px">No active positions — observing market consensus</td></tr>';
    return;
  }
  tb.innerHTML = positions.map(p => {
    const pnlCls = p.unrealized_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const sign = p.unrealized_pnl >= 0 ? '+' : '';
    return `<tr>
      <td style="color:var(--muted)">${p.trade_id.slice(-6)}</td>
      <td class="${p.direction.toLowerCase()}">${p.direction}</td>
      <td>$${p.entry_price.toFixed(2)}</td>
      <td>$${p.current_price.toFixed(2)}</td>
      <td>${p.size} oz</td>
      <td style="color:var(--red)">$${p.sl.toFixed(2)}</td>
      <td style="color:var(--green)">$${p.tp.toFixed(2)}</td>
      <td class="${pnlCls}">${sign}$${p.unrealized_pnl.toFixed(3)}</td>
    </tr>`;
  }).join('');
}

function renderTrades(trades) {
  const tb = document.getElementById('trades-body');
  const closed = trades.filter(t => t.status === 'CLOSED').slice(-15).reverse();
  if (!closed.length) {
    tb.innerHTML = '<tr><td colspan="8" style="color:var(--muted);text-align:center;padding:8px">No closed trades yet</td></tr>';
    return;
  }

  // Toast if new trade closed
  if (lastKnownTradeCount !== -1 && closed.length > lastKnownTradeCount) {
    const newest = closed[0];
    const pnl = newest.realized_pnl || 0;
    const msg = `Trade Closed: <b>${newest.direction}</b> | PnL: <b>${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</b> (${newest.exit_reason})`;
    showToast(msg, pnl >= 0 ? 'success' : 'error');
  }
  lastKnownTradeCount = closed.length;

  tb.innerHTML = closed.map(t => {
    const pnlCls = t.realized_pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
    const sign   = t.realized_pnl >= 0 ? '+' : '';
    const time   = (t.exit_time || '').slice(11,19);
    return `<tr>
      <td style="color:var(--muted)">${t.trade_id.slice(-6)}</td>
      <td class="${t.direction.toLowerCase()}">${t.direction}</td>
      <td>$${t.entry_price.toFixed(2)}</td>
      <td>$${(t.exit_price||0).toFixed(2)}</td>
      <td>${t.size} oz</td>
      <td class="${pnlCls}">${sign}$${(t.realized_pnl||0).toFixed(4)}</td>
      <td style="font-size:8.5px;color:var(--subtext)">${t.exit_reason||''}</td>
      <td style="color:var(--muted)">${time}</td>
    </tr>`;
  }).join('');
}

function renderKnowledgeSummary(ks) {
  if (!ks) return;
  document.getElementById('kb-winrate').textContent = ((ks.knowledge_win_rate||0)*100).toFixed(1)+'%';
  const pnl = ks.total_knowledge_pnl || 0;
  const pnlEl = document.getElementById('kb-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
  pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('kb-trades-badge').textContent = (ks.total_trades_logged || 0) + ' Setups Logged';
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

// ── Master Data Ingestion ────────────────────────────────────────────────────
function onData(d) {
  const price = d.spot_price || 0;
  const spotEl = document.getElementById('spot');
  spotEl.textContent = '$' + price.toFixed(2);

  if (prevPrice && price !== prevPrice) {
    const chg = price - prevPrice;
    const el = document.getElementById('price-chg');
    el.textContent = (chg >= 0 ? '▲ +' : '▼ ') + chg.toFixed(2);
    el.className = 'spot-chg ' + (chg >= 0 ? 'badge-green' : 'badge-red');
    spotEl.classList.remove('flash-green', 'flash-red');
    void spotEl.offsetWidth;
    spotEl.classList.add(chg >= 0 ? 'flash-green' : 'flash-red');
  }
  prevPrice = price;

  // Candlestick update
  const now = Math.floor(Date.now()/1000);
  const candleTime = now - (now % 60);
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
  document.getElementById('l-iters').textContent = d.learner_iters || 0;

  // Cooldown badge
  const cdRem = d.cooldown_remaining || 0;
  const cdBadge = document.getElementById('cooldown-badge');
  if (cdRem > 0) {
    cdBadge.style.display = '';
    cdBadge.textContent = `⏳ COOLDOWN ${cdRem}s`;
  } else {
    cdBadge.style.display = 'none';
  }

  // Bot status & button
  const statusEl = document.getElementById('bot-status');
  const btnToggle = document.getElementById('btn-toggle');
  statusEl.textContent = d.is_active ? 'SYSTEM LIVE' : 'PAUSED';
  statusEl.className = 'badge ' + (d.is_active ? 'badge-green' : 'badge-red');
  btnToggle.textContent = d.is_active ? '⏸ Pause Bot' : '▶ Resume Bot';
  document.getElementById('circuit-badge').style.display = d.circuit_breaker ? '' : 'none';

  // Stats
  const acc = d.account || {};
  const fmt = v => '$' + (v||0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
  // Factual Technicals & Knowledge HUD
  const facts = d.factual_indicators || {};
  if (facts.atr) document.getElementById('hud-atr').textContent = '$' + Number(facts.atr).toFixed(2);
  if (facts.rsi) document.getElementById('hud-rsi').textContent = Number(facts.rsi).toFixed(1);

  const ks = d.knowledge_summary || {};
  if (ks.profit_factor) document.getElementById('hud-pf').textContent = Number(ks.profit_factor).toFixed(2);

  const kbIntel = d.kb_intelligence || {};
  if (kbIntel.rationale) {
    document.getElementById('kb-intel-text').textContent = '📚 ' + kbIntel.rationale;
  }
  if (kbIntel.multiplier) {
    document.getElementById('kb-intel-badge').textContent = Number(kbIntel.multiplier).toFixed(2) + 'x Sizing';
    document.getElementById('kb-intel-badge').className = 'badge ' + (kbIntel.multiplier > 1.0 ? 'badge-green' : kbIntel.multiplier < 1.0 ? 'badge-red' : 'badge-gold');
  }

  renderSignals(d);
  setGauge(d.ap_overall || 0);
  renderTargets(d.targets || []);
  renderWeights(d.learner_weights || {});
  renderPositions(d.positions || []);
  renderTrades(d.recent_trades || []);
  renderKnowledgeSummary(d.knowledge_summary);
  renderPocHistory(d.poc_history);
}

// ── Networking (WebSocket + Fallback Polling) ────────────────────────────────
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
setInterval(fetchState, 1500);

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
    uvicorn.run(app, host=host, port=port, log_level="info")
