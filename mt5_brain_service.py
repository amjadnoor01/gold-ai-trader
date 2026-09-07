"""
mt5_brain_service.py — High-Frequency Local SGD AI Brain Microservice.

Endpoints:
  - POST /predict: Ingests tick features (rsi, macd_diff, ema_slope, volatility, adx),
                   returns market regime, direction, dynamic threshold, confidence, and
                   Directional Strength Score (-100 to +100).
  - POST /feedback: Online warm-start partial_fit SGD training on trade P&L.
  - GET  /metrics: Model win rate, L2 regularization state, telemetry.
  - POST /cluster_trigger: Evaluates multi-tranche trigger when strength >= 35%.

Database: ~/trading_poc.db
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH = Path("/Users/amjadnoor/trading_poc.db")
MQL5_FILES = Path("/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files")
MQL5_FILES.mkdir(parents=True, exist_ok=True)
IPC_SIGNAL_FILE = MQL5_FILES / "ai_signal.json"
COMMAND_TRIGGER_FILE = MQL5_FILES / "cluster_command.json"

app = FastAPI(title="MT5 High-Frequency Local SGD Brain")

# ── SQLite Setup ──────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                rsi REAL, macd_diff REAL, ema_slope REAL, volatility REAL, adx REAL,
                strength_score REAL NOT NULL,
                direction INTEGER NOT NULL,
                confidence REAL NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trade_id TEXT,
                direction INTEGER NOT NULL,
                realized_pnl REAL NOT NULL,
                rsi REAL, macd_diff REAL, ema_slope REAL, volatility REAL, adx REAL,
                learned_label INTEGER NOT NULL
            );
        """)
        for tbl in ["predictions", "feedback"]:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN adx REAL;")
            except sqlite3.OperationalError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cluster_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                strength_score REAL NOT NULL,
                direction TEXT NOT NULL,
                tranches_json TEXT NOT NULL
            );
        """)
        conn.commit()

init_db()

# ── Online SGD Classifier ─────────────────────────────────────────────────────
class SGDBrain:
    def __init__(self):
        self.scaler = StandardScaler()
        self.clf = SGDClassifier(loss="log_loss", penalty="l2", alpha=0.0001, warm_start=True, random_state=42)
        self.is_fitted = False
        self.feedback_count = 0
        self.wins = 0
        self.losses = 0

        # Pre-fit dummy values [rsi, macd_diff, ema_slope, volatility, adx]
        X_dummy = np.array([
            [30.0, -0.5, -0.1, 0.001, 15.0],
            [70.0,  0.5,  0.1, 0.005, 35.0],
            [45.0, -0.2, -0.05, 0.002, 18.0],
            [55.0,  0.2,  0.05, 0.003, 30.0],
        ])
        y_dummy = np.array([0, 1, 0, 1])
        self.scaler.fit(X_dummy)
        self.clf.partial_fit(self.scaler.transform(X_dummy), y_dummy, classes=np.array([0, 1]))
        self.is_fitted = True

    def predict(self, rsi: float, macd_diff: float, ema_slope: float, volatility: float, adx: float = 25.0) -> dict:
        features = np.array([[rsi, macd_diff, ema_slope, volatility, adx]])
        feats_scaled = self.scaler.transform(features)

        # Class probabilities: [P(Loss/Sell), P(Win/Buy)]
        probs = self.clf.predict_proba(feats_scaled)[0]
        prob_buy  = float(probs[1])
        prob_sell = float(probs[0])

        # Market Regime Detection
        if adx >= 25.0:
            regime = "TRENDING_EXPANSION"
            regime_mult = 1.15
        elif adx <= 18.0:
            regime = "MEAN_REVERSION_RANGE"
            regime_mult = 0.85
        else:
            regime = "TRANSITIONAL"
            regime_mult = 1.0

        # Directional Strength Score: -100 to +100
        raw_score = (prob_buy - prob_sell) * 100.0 * regime_mult
        strength_score = round(float(np.clip(raw_score, -100.0, 100.0)), 2)

        if strength_score >= 25.0:
            direction = 1   # BUY
            confidence = prob_buy
        elif strength_score <= -25.0:
            direction = -1  # SELL
            confidence = prob_sell
        else:
            direction = 0   # FLAT / NEUTRAL
            confidence = max(prob_buy, prob_sell)

        threshold = 35.0  # Dynamic trigger threshold

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "rsi": rsi,
            "macd_diff": macd_diff,
            "ema_slope": ema_slope,
            "volatility": volatility,
            "adx": adx,
            "regime": regime,
            "strength_score": strength_score,
            "direction": direction,
            "confidence": round(confidence, 4),
            "dynamic_threshold": threshold,
            "cluster_eligible": abs(strength_score) >= threshold,
        }

        # Mirror to IPC file
        try:
            IPC_SIGNAL_FILE.write_text(json.dumps(result, indent=2))
        except Exception as e:
            logger.error(f"[IPC] Failed to write IPC file: {e}")

        # Save prediction to SQLite
        with get_db() as conn:
            conn.execute("""
                INSERT INTO predictions (timestamp, rsi, macd_diff, ema_slope, volatility, adx, strength_score, direction, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (result["timestamp"], rsi, macd_diff, ema_slope, volatility, adx, strength_score, direction, confidence))
            conn.commit()

        return result

    def learn_feedback(self, trade_id: str, direction: int, realized_pnl: float, rsi: float, macd_diff: float, ema_slope: float, volatility: float, adx: float = 25.0):
        # 1 = Win, 0 = Loss
        label = 1 if realized_pnl > 0 else 0
        if realized_pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.feedback_count += 1

        X = np.array([[rsi, macd_diff, ema_slope, volatility, adx]])
        X_scaled = self.scaler.transform(X)
        self.clf.partial_fit(X_scaled, np.array([label]))

        with get_db() as conn:
            conn.execute("""
                INSERT INTO feedback (timestamp, trade_id, direction, realized_pnl, rsi, macd_diff, ema_slope, volatility, adx, learned_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (datetime.now(timezone.utc).isoformat(), trade_id, direction, realized_pnl, rsi, macd_diff, ema_slope, volatility, adx, label))
            conn.commit()

        logger.info(f"[SGD Learn] Trade {trade_id} feedback processed | PnL={realized_pnl:+.2f} | label={label} | total_feedback={self.feedback_count}")

brain = SGDBrain()

# ── Data Models ───────────────────────────────────────────────────────────────
class PredictRequest(BaseModel):
    rsi: float = 50.0
    macd_diff: float = 0.0
    ema_slope: float = 0.0
    volatility: float = 0.002
    adx: float = 25.0

class FeedbackRequest(BaseModel):
    trade_id: str = "XAU_001"
    direction: int = 1
    realized_pnl: float = 12.50
    rsi: float = 50.0
    macd_diff: float = 0.0
    ema_slope: float = 0.0
    volatility: float = 0.002
    adx: float = 25.0

class ClusterTriggerRequest(BaseModel):
    strength_score: float
    direction: str = "BUY"

# ── API Endpoints ─────────────────────────────────────────────────────────────
@app.post("/predict")
async def predict_endpoint(req: PredictRequest):
    return brain.predict(req.rsi, req.macd_diff, req.ema_slope, req.volatility, req.adx)

@app.post("/feedback")
async def feedback_endpoint(req: FeedbackRequest):
    brain.learn_feedback(req.trade_id, req.direction, req.realized_pnl, req.rsi, req.macd_diff, req.ema_slope, req.volatility, req.adx)
    return {"status": "learned", "feedback_count": brain.feedback_count}

@app.get("/metrics")
async def metrics_endpoint():
    total = brain.wins + brain.losses
    win_rate = (brain.wins / total) if total > 0 else 0.0
    return {
        "is_fitted": brain.is_fitted,
        "feedback_count": brain.feedback_count,
        "wins": brain.wins,
        "losses": brain.losses,
        "win_rate": round(win_rate, 4),
        "l2_alpha": brain.clf.alpha,
        "model_coef": brain.clf.coef_.tolist() if hasattr(brain.clf, "coef_") else [],
        "database_path": str(DB_PATH),
    }

@app.post("/cluster_trigger")
async def cluster_trigger_endpoint(req: ClusterTriggerRequest):
    if abs(req.strength_score) < 35.0:
        raise HTTPException(status_code=400, detail="Institutional strength score below 35.0 threshold")

    cluster_id = f"CL-{int(datetime.now(timezone.utc).timestamp()) % 10000}"
    direction = "BUY" if req.strength_score >= 35.0 else "SELL"

    tranches = [
        {"name": "Tranche 1 (Alpha Scalp)", "lots": 0.02, "tp_pips": 18, "trailing_stop": True},
        {"name": "Tranche 2 (Core Trend)", "lots": 0.02, "tp_pips": 32, "trailing_stop": False},
        {"name": "Tranche 3 (Impulse Runner)", "lots": 0.01, "tp_pips": 55, "trailing_stop": False},
    ]

    trigger_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cluster_id": cluster_id,
        "direction": direction,
        "strength_score": req.strength_score,
        "tranches": tranches
    }

    try:
        COMMAND_TRIGGER_FILE.write_text(json.dumps(trigger_payload, indent=2))
    except Exception as e:
        logger.error(f"[IPC Command] Failed writing cluster_command.json: {e}")

    with get_db() as conn:
        conn.execute("""
            INSERT INTO cluster_triggers (timestamp, cluster_id, strength_score, direction, tranches_json)
            VALUES (?, ?, ?, ?, ?);
        """, (trigger_payload["timestamp"], cluster_id, req.strength_score, direction, json.dumps(tranches)))
        conn.commit()

    return trigger_payload

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
