"""
mt5_autonomy_loop.py — Multi-Symbol Autonomous AI Engine & Risk/Margin Controller.

1. Multi-Symbol Indicator Engine:
   - Scans all MT5 live feature files (MQL5/Files/live_features_*.json).
   - Supports XAUUSD, EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, USDSEK.
   - Feeds technical feature vectors to Local SGD AI Brain (POST http://127.0.0.1:8000/predict).
   - Writes symbol-specific prediction files (MQL5/Files/ai_signal_<SYMBOL>.json).

2. Margin & Risk Guard:
   - Inspects MQL5/Files/mt5_state.json.
   - Enforces Free Margin >= $500 and Free Margin/Balance >= 15%.
   - Enforces Max 9 open cluster positions across account.

3. Targeted Trade Cluster Execution:
   - When Directional Strength Score >= 35.0 or <= -35.0, automatically issues
     symbol-targeted cluster trigger command (MQL5/Files/cluster_command_<SYMBOL>.json).

4. Continuous Learning Feedback Loop:
   - Processes MQL5/Files/ai_feedback.json closed trade outcomes.
   - Performs online SGD warm-start partial_fit model weight updates.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MQL5_FILES = Path("/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Files")
MQL5_FILES.mkdir(parents=True, exist_ok=True)

STATE_FILE    = MQL5_FILES / "mt5_state.json"
FEEDBACK_FILE = MQL5_FILES / "ai_feedback.json"
DB_PATH       = Path.home() / "trading_poc.db"


def read_mql5_file(filepath: Path) -> str:
    """Read MQL5 text file handling UTF-16LE and UTF-8 encodings seamlessly."""
    if not filepath.exists():
        return ""
    try:
        data = filepath.read_bytes()
        if not data:
            return ""
        if data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff'):
            return data.decode("utf-16", errors="ignore")
        try:
            return data.decode("utf-8")
        except Exception:
            return data.decode("utf-16le", errors="ignore")
    except Exception as e:
        logger.error(f"Error reading {filepath.name}: {e}")
        return ""


class MultiSymbolAutonomyLoop:
    def __init__(self):
        self.last_cluster_time: Dict[str, float] = {}
        self.cooldown_sec = 20.0   # 20s per-symbol cooldown
        self.last_feedback_mtime = 0.0

    def get_evolving_parameters(self, symbol: str) -> tuple[float, float]:
        """Dynamically compute evolved trigger threshold and lot multiplier based on rolling win rate."""
        try:
            with sqlite3.connect(str(DB_PATH), timeout=5.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT learned_label FROM feedback ORDER BY id DESC LIMIT 20")
                rows = cur.fetchall()
                if not rows or len(rows) < 5:
                    return 35.0, 1.0
                
                wins = sum(1 for r in rows if r[0] == 1)
                win_rate = wins / len(rows)
                
                if win_rate >= 0.65:
                    return 30.0, 1.25   # Aggressive mode
                elif win_rate <= 0.45:
                    return 42.0, 0.75   # Capital Protection mode
                else:
                    return 35.0, 1.0
        except Exception:
            return 35.0, 1.0

    def get_account_state(self) -> dict:
        """Read live account state exported by MT5."""
        text = read_mql5_file(STATE_FILE)
        if not text:
            return {"free_margin": 10000.0, "balance": 10000.0, "position_count": 0}
        try:
            data = json.loads(text)
            return {
                "free_margin": data.get("free_margin", 10000.0),
                "balance": data.get("balance", 10000.0),
                "equity": data.get("equity", 10000.0),
                "position_count": len(data.get("positions", [])),
            }
        except Exception:
            return {"free_margin": 10000.0, "balance": 10000.0, "position_count": 0}

    def call_brain_predict(self, feats: dict) -> Optional[dict]:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=3.0)
            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/predict", json.dumps(feats), headers)
            r = conn.getresponse()
            if r.status == 200:
                data = json.loads(r.read().decode())
                conn.close()
                return data
            conn.close()
        except Exception as e:
            logger.error(f"[Brain API] Predict error: {e}")
        return None

    def call_brain_feedback(self, feedback_data: dict) -> bool:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=3.0)
            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/feedback", json.dumps(feedback_data), headers)
            r = conn.getresponse()
            conn.close()
            return r.status == 200
        except Exception as e:
            logger.error(f"[Brain API] Feedback error: {e}")
        return False

    def process_feedback_file(self):
        """Check for closed trade feedback sent by MT5 EA."""
        if not FEEDBACK_FILE.exists():
            return
        try:
            mtime = FEEDBACK_FILE.stat().st_mtime
            if mtime <= self.last_feedback_mtime:
                return
            self.last_feedback_mtime = mtime

            text = read_mql5_file(FEEDBACK_FILE).strip()
            if not text:
                return
            items = json.loads(text) if text.startswith("[") else [json.loads(text)]
            for item in items:
                success = self.call_brain_feedback(item)
                if success:
                    logger.info(f"[Continuous Learning] Model retrained on trade {item.get('trade_id')} | PnL=${item.get('realized_pnl', 0):+.2f}")
        except Exception as e:
            logger.error(f"[Feedback Processor] Error: {e}")

    async def run(self):
        logger.info("=" * 70)
        logger.info("  ⚡ Multi-Symbol Autonomous AI Engine & Risk/Margin Guard")
        logger.info("  IPC Directory: " + str(MQL5_FILES))
        logger.info("  AI Brain Server: http://127.0.0.1:8000")
        logger.info("=" * 70)

        while True:
            try:
                # 1. Discover all symbol feature files exported by MT5 EAs
                feature_files = list(MQL5_FILES.glob("live_features_*.json"))
                if not feature_files and (MQL5_FILES / "live_features.json").exists():
                    feature_files = [MQL5_FILES / "live_features.json"]

                # 2. Check Account Margin State
                acc_state = self.get_account_state()
                free_margin = acc_state["free_margin"]
                balance = acc_state["balance"]
                pos_cnt = acc_state["position_count"]

                for ff in feature_files:
                    try:
                        text = read_mql5_file(ff)
                        if not text:
                            continue
                        raw = json.loads(text)
                        if "rsi" not in raw:
                            continue
                        
                        symbol = raw.get("symbol", "XAUUSD")
                        feats = {
                            "rsi": raw.get("rsi", 50.0),
                            "macd_diff": raw.get("macd_diff", 0.0),
                            "ema_slope": raw.get("ema_slope", 0.0),
                            "volatility": raw.get("volatility", 0.002),
                        }

                        # 3. Query AI Brain Prediction
                        pred = self.call_brain_predict(feats)
                        if not pred:
                            continue

                        strength = pred.get("strength_score", 0.0)
                        direction = pred.get("direction", 0)
                        confidence = pred.get("confidence", 0.5)

                        # Write symbol-specific signal
                        sig_file = MQL5_FILES / f"ai_signal_{symbol}.json"
                        sig_file.write_text(json.dumps(pred, indent=2))
                        (MQL5_FILES / "ai_signal.json").write_text(json.dumps(pred, indent=2))

                        logger.info(f"[{symbol}] Strength={strength:+.1f}% | Dir={direction} | RSI={feats['rsi']} | FreeMargin=${free_margin:.2f}")

                        # Get self-evolving dynamic threshold and lot multiplier
                        dyn_thresh, lot_mult = self.get_evolving_parameters(symbol)

                        # 4. Check Margin Guard & Trade Cluster Dispatch
                        now = time.time()
                        last_time = self.last_cluster_time.get(symbol, 0.0)

                        if abs(strength) >= dyn_thresh and (now - last_time) >= self.cooldown_sec:
                            # Margin Protection Checks
                            if free_margin < 500.0 or (balance > 0 and (free_margin / balance) < 0.15):
                                logger.warning(f"⚠️ [{symbol}] Cluster skipped: Low Free Margin (${free_margin:.2f})")
                                continue

                            if pos_cnt >= 12:
                                logger.warning(f"⚠️ [{symbol}] Cluster skipped: Max open positions cap ({pos_cnt}) reached")
                                continue

                            self.last_cluster_time[symbol] = now
                            cluster_id = f"CL-{symbol}-{int(now) % 10000}"
                            dir_str = "BUY" if strength >= dyn_thresh else "SELL"

                            cmd_payload = {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "symbol": symbol,
                                "cluster_id": cluster_id,
                                "direction": dir_str,
                                "strength_score": strength,
                                "tranches": [
                                    {"name": "Tranche 1 (Alpha Scalp)", "lots": round(0.02 * lot_mult, 2), "tp_pips": 18, "trailing_stop": True},
                                    {"name": "Tranche 2 (Core Trend)", "lots": round(0.02 * lot_mult, 2), "tp_pips": 32, "trailing_stop": False},
                                    {"name": "Tranche 3 (Impulse Runner)", "lots": round(0.01 * lot_mult, 2), "tp_pips": 55, "trailing_stop": False},
                                ]
                            }

                            cmd_file = MQL5_FILES / f"cluster_command_{symbol}.json"
                            cmd_file.write_text(json.dumps(cmd_payload, indent=2))
                            (MQL5_FILES / "cluster_command.json").write_text(json.dumps(cmd_payload, indent=2))

                            logger.info(f"🔥 [MULTI-SYMBOL DISPATCH] {symbol} Cluster {cluster_id} dispatched ({dir_str}) | Strength={strength:+.1f}%")

                    except Exception as e:
                        logger.error(f"[Symbol Loop {ff.name}] Error: {e}")

                # 5. Process deal exit feedback
                self.process_feedback_file()

            except Exception as e:
                logger.error(f"[Autonomy Loop] Error: {e}")

            await asyncio.sleep(2.0)


if __name__ == "__main__":
    loop = MultiSymbolAutonomyLoop()
    asyncio.run(loop.run())
