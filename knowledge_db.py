"""
knowledge_db.py — Persistent SQLite Knowledge Base & POC Audit Engine.

Tracks:
  1. Detailed trade execution history & market setup features
  2. Experience memory & pattern similarity lookup (KNN-based setup filter)
  3. Proof-of-Concept (POC) performance audit milestones
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DB_PATH = Path("data/trading_knowledge.db")


class KnowledgeDatabase:
    """
    SQLite-backed Knowledge Base & POC Audit System.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        """Initialize database tables for Trades, Knowledge Memory, and POC Records."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            # 1. Trades table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    size REAL NOT NULL,
                    sl REAL NOT NULL,
                    tp REAL NOT NULL,
                    realized_pnl REAL,
                    return_pct REAL,
                    confidence REAL,
                    regime TEXT,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    exit_reason TEXT,
                    features_json TEXT,
                    lessons_learned TEXT
                );
            """)

            # 2. Market Setup Knowledge Memory (for pattern matching)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL NOT NULL,
                    atr REAL,
                    rsi REAL,
                    volatility REAL,
                    trend_strength REAL,
                    outcome TEXT NOT NULL,  -- 'WIN' | 'LOSS'
                    pnl REAL NOT NULL,
                    FOREIGN KEY (trade_id) REFERENCES trades (trade_id)
                );
            """)

            # 3. Proof of Concept (POC) Milestone Audit Records
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poc_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    total_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    equity REAL NOT NULL,
                    cumulative_pnl REAL NOT NULL,
                    sharpe_ratio REAL NOT NULL,
                    max_drawdown REAL NOT NULL,
                    profit_factor REAL NOT NULL,
                    active_weights_json TEXT NOT NULL,
                    notes TEXT
                );
            """)

            conn.commit()
        logger.info(f"[KnowledgeDB] SQLite database initialized at {self.db_path}")

    # ── Trade & Knowledge Memory Logging ─────────────────────────────────────
    def log_trade_opened(self, trade: dict, features: Optional[dict] = None):
        """Record trade opening in SQLite database."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, symbol, direction, entry_price, size, sl, tp,
                    confidence, regime, entry_time, features_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                trade["trade_id"],
                trade.get("symbol", "XAUUSD"),
                trade["direction"],
                trade["entry_price"],
                trade["size"],
                trade["sl"],
                trade["tp"],
                trade.get("confidence", 0.7),
                trade.get("regime", "DRL_PPO"),
                trade.get("entry_time", datetime.now(timezone.utc).isoformat()),
                json.dumps(features) if features else None
            ))
            conn.commit()

    def log_trade_closed(self, trade: dict, market_context: Optional[dict] = None) -> str:
        """
        Record trade exit, compute lessons learned, and store in Knowledge Memory.
        """
        pnl = float(trade.get("realized_pnl", 0.0))
        outcome = "WIN" if pnl > 0 else "LOSS"
        lessons = (
            f"Trade profitable (+${pnl:.2f}). Setup confirmed."
            if pnl > 0
            else f"Trade stopped out (-${abs(pnl):.2f}). High volatility or bad entry."
        )

        with self._get_conn() as conn:
            # Update main trade row
            conn.execute("""
                UPDATE trades SET
                    exit_price = ?,
                    realized_pnl = ?,
                    return_pct = ?,
                    exit_time = ?,
                    exit_reason = ?,
                    lessons_learned = ?
                WHERE trade_id = ?;
            """, (
                trade.get("exit_price"),
                pnl,
                trade.get("return_pct", pnl / 100.0),
                trade.get("exit_time", datetime.now(timezone.utc).isoformat()),
                trade.get("exit_reason", "CLOSED"),
                lessons,
                trade["trade_id"]
            ))

            # Add to knowledge memory for pattern similarity matching
            ctx = market_context or {}
            conn.execute("""
                INSERT INTO knowledge_memory (
                    trade_id, timestamp, direction, price, atr, rsi,
                    volatility, trend_strength, outcome, pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                trade["trade_id"],
                datetime.now(timezone.utc).isoformat(),
                trade["direction"],
                float(trade.get("entry_price", 0.0)),
                float(ctx.get("atr", 5.0)),
                float(ctx.get("rsi", 50.0)),
                float(ctx.get("volatility", 0.01)),
                float(ctx.get("trend_strength", 0.5)),
                outcome,
                pnl
            ))

            conn.commit()
        logger.info(f"[KnowledgeDB] Trade {trade['trade_id']} logged to Knowledge Base | Outcome: {outcome} (${pnl:+.2f})")
        return lessons

    # ── Knowledge Intelligence (Pattern Matching) ────────────────────────────
    def query_setup_intelligence(self, current_context: dict) -> Tuple[float, str]:
        """
        Compare current market setup against historical knowledge memory.
        Returns: (confidence_modifier: float, rationale: str)
          - confidence_modifier: 0.5 to 1.2 multiplier for trade sizing
          - rationale: explanation from past knowledge
        """
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT direction, atr, rsi, outcome, pnl
                FROM knowledge_memory
                ORDER BY id DESC LIMIT 100;
            """).fetchall()

        if len(rows) < 5:
            return 1.0, "Knowledge Base initializing — insufficient historical samples."

        curr_atr = float(current_context.get("atr", 5.0))
        curr_rsi = float(current_context.get("rsi", 50.0))
        curr_dir = current_context.get("direction", "BUY")

        # Find 5 nearest market contexts
        distances = []
        for r in rows:
            if r["direction"] != curr_dir:
                continue
            # Euclidean distance over normalized ATR and RSI
            d_atr = ((r["atr"] - curr_atr) / max(curr_atr, 1.0)) ** 2
            d_rsi = ((r["rsi"] - curr_rsi) / 100.0) ** 2
            dist = np.sqrt(d_atr + d_rsi)
            distances.append((dist, r["outcome"], r["pnl"]))

        if not distances:
            return 1.0, "No historical direction match found."

        distances.sort(key=lambda x: x[0])
        top_k = distances[:5]
        wins = sum(1 for _, outcome, _ in top_k if outcome == "WIN")
        historical_win_rate = wins / len(top_k)

        if historical_win_rate >= 0.8:
            return 1.15, f"Knowledge Base HIGH CONFIDENCE: Similar setups have {wins}/{len(top_k)} wins."
        elif historical_win_rate <= 0.2:
            return 0.60, f"Knowledge Base CAUTION: Similar setups had high failure rate ({wins}/{len(top_k)} wins)."
        else:
            return 1.0, f"Knowledge Base NEUTRAL: Similar setups win rate {historical_win_rate:.0%}."

    # ── POC Record Keeping ───────────────────────────────────────────────────
    def record_poc_milestone(self, account: dict, metrics: dict, weights: dict, notes: str = "Automated POC Audit"):
        """Record a structured Proof-of-Concept milestone audit entry."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO poc_records (
                    timestamp, total_trades, win_rate, equity, cumulative_pnl,
                    sharpe_ratio, max_drawdown, profit_factor, active_weights_json, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                datetime.now(timezone.utc).isoformat(),
                int(account.get("closed_trades", 0)),
                float(metrics.get("win_rate", 0.0)),
                float(account.get("equity", 10000.0)),
                float(account.get("realized_pnl", 0.0)),
                float(metrics.get("sharpe", 0.0)),
                float(metrics.get("max_drawdown", 0.0)),
                float(metrics.get("profit_factor", 0.0)),
                json.dumps(weights),
                notes
            ))
            conn.commit()
        logger.info(f"[KnowledgeDB] POC Audit Record logged | Equity: ${account.get('equity', 10000):,.2f}")

    def get_poc_history(self, limit: int = 20) -> List[dict]:
        """Fetch recent POC audit milestone records."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM poc_records ORDER BY id DESC LIMIT ?;
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_knowledge_summary(self) -> dict:
        """Get high level knowledge base statistics."""
        with self._get_conn() as conn:
            total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            closed_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL").fetchone()[0]
            winning_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE realized_pnl > 0").fetchone()[0]
            total_pnl = conn.execute("SELECT SUM(realized_pnl) FROM trades").fetchone()[0] or 0.0
            total_poc_audits = conn.execute("SELECT COUNT(*) FROM poc_records").fetchone()[0]

        win_rate = (winning_trades / closed_trades) if closed_trades > 0 else 0.0
        return {
            "total_trades_logged": total_trades,
            "closed_trades": closed_trades,
            "winning_trades": winning_trades,
            "knowledge_win_rate": round(win_rate, 4),
            "total_knowledge_pnl": round(total_pnl, 2),
            "poc_audit_records": total_poc_audits,
        }
