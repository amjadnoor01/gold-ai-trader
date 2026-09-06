"""
autopilot.py — Performance targets + self-tuning engine.

Targets:
  - Win rate      ≥ 55%
  - Sharpe ratio  ≥ 1.2  (annualised)
  - Max drawdown  < 5%
  - Avg R:R       ≥ 1.8
  - Profit factor ≥ 1.5

After every N closed trades the autopilot evaluates performance,
scores each target, and writes a tuning directive to the bot
(adjusting risk %, SL/TP multiplier, cooldown) so the bot
self-corrects without human intervention.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── File paths ────────────────────────────────────────────────────────────────
STATE_FILE    = Path("logs/paper_state.json")
TARGETS_FILE  = Path("logs/autopilot_targets.json")
PERF_LOG      = Path("logs/performance_log.json")
TUNE_FILE     = Path("logs/tune_directive.json")

for f in [TARGETS_FILE, PERF_LOG, TUNE_FILE]:
    f.parent.mkdir(parents=True, exist_ok=True)

# ── Performance targets ───────────────────────────────────────────────────────
DEFAULT_TARGETS = {
    "win_rate":      {"target": 0.55, "min": 0.45, "label": "Win Rate",      "unit": "%",  "scale": 100},
    "sharpe":        {"target": 1.20, "min": 0.50, "label": "Sharpe Ratio",  "unit": "",   "scale": 1},
    "max_drawdown":  {"target": 0.05, "min": 0.10, "label": "Max Drawdown",  "unit": "%",  "scale": 100, "lower_is_better": True},
    "avg_rr":        {"target": 1.80, "min": 1.00, "label": "Avg R:R",       "unit": "x",  "scale": 1},
    "profit_factor": {"target": 1.50, "min": 1.00, "label": "Profit Factor", "unit": "x",  "scale": 1},
}


class PerformanceTarget:
    def __init__(self, key: str, cfg: dict):
        self.key    = key
        self.target = cfg["target"]
        self.min    = cfg["min"]
        self.label  = cfg["label"]
        self.unit   = cfg["unit"]
        self.scale  = cfg.get("scale", 1)
        self.lower_is_better = cfg.get("lower_is_better", False)

    def score(self, value: float) -> float:
        """Returns 0.0–1.0: 1.0 = at or above target."""
        if self.lower_is_better:
            if value <= self.target:
                return 1.0
            if value >= self.min:
                return 0.0
            return 1.0 - (value - self.target) / (self.min - self.target)
        else:
            if value >= self.target:
                return 1.0
            if value <= self.min:
                return 0.0
            return (value - self.min) / (self.target - self.min)

    def status(self, value: float) -> str:
        s = self.score(value)
        if s >= 0.90:
            return "✅ ON TARGET"
        if s >= 0.60:
            return "⚠️ CLOSE"
        return "❌ BELOW"


class Autopilot:
    """
    Evaluates every N closed trades, computes performance metrics,
    scores against targets, then generates a tuning directive for the bot.
    """

    EVAL_EVERY = 5   # evaluate after every 5 closed trades

    def __init__(self):
        self.targets = {k: PerformanceTarget(k, v) for k, v in DEFAULT_TARGETS.items()}
        self._last_eval_count = 0
        self._perf_history: List[dict] = self._load_perf_history()

    # ── Public API ────────────────────────────────────────────────────────────
    def evaluate(self, trades: List[dict], account: dict) -> Optional[dict]:
        """
        Call with the full closed trades list after each tick.
        Returns evaluation dict if a new evaluation ran, else None.
        """
        n = len(trades)
        if n < 3 or (n - self._last_eval_count) < self.EVAL_EVERY:
            return None

        self._last_eval_count = n
        metrics   = self._compute_metrics(trades, account)
        scores    = {k: self.targets[k].score(metrics[k]) for k in self.targets}
        overall   = float(np.mean(list(scores.values())))
        directive = self._generate_directive(metrics, scores)

        report = {
            "timestamp":  datetime.utcnow().isoformat(),
            "trade_count": n,
            "metrics":    metrics,
            "scores":     scores,
            "overall_score": overall,
            "directive":  directive,
            "status": {k: self.targets[k].status(metrics[k]) for k in self.targets},
        }

        self._perf_history.append(report)
        self._save(report)
        logger.info(f"[Autopilot] Eval #{n} trades  overall={overall:.1%}  "
                    f"directive={directive}")
        return report

    def get_targets_display(self, metrics: Optional[dict] = None) -> List[dict]:
        """Returns displayable list of targets with current values."""
        out = []
        for k, tgt in self.targets.items():
            val = metrics.get(k, 0.0) if metrics else 0.0
            out.append({
                "key":    k,
                "label":  tgt.label,
                "target": tgt.target * tgt.scale,
                "value":  round(val * tgt.scale, 2),
                "unit":   tgt.unit,
                "score":  round(tgt.score(val), 3),
                "status": tgt.status(val),
                "lower_is_better": tgt.lower_is_better,
            })
        return out

    # ── Metrics ───────────────────────────────────────────────────────────────
    def _compute_metrics(self, trades: List[dict], account: dict) -> dict:
        pnls     = [float(t.get("realized_pnl", 0)) for t in trades]
        wins     = [p for p in pnls if p > 0]
        losses   = [p for p in pnls if p < 0]

        win_rate = len(wins) / len(pnls) if pnls else 0.0

        # Sharpe (annualised, assume each trade ~1h)
        pnl_arr  = np.array(pnls)
        sharpe   = 0.0
        if len(pnl_arr) >= 3 and pnl_arr.std() > 0:
            sharpe = float((pnl_arr.mean() / pnl_arr.std()) * math.sqrt(252 * 6.5))

        # Max drawdown from equity curve
        equity   = float(account.get("equity", 10000))
        balance  = float(account.get("balance", 10000))
        peak     = max(10000.0, equity)
        max_dd   = max(0.0, (peak - equity) / peak)

        # Avg R:R from closed trades that have sl/tp fields
        rr_list  = []
        for t in trades:
            ep = float(t.get("entry_price", 0))
            ex = float(t.get("exit_price", 0))
            sl = float(t.get("sl", 0))
            tp = float(t.get("tp", 0))
            if ep > 0 and sl > 0 and tp > 0:
                risk   = abs(ep - sl)
                reward = abs(ep - tp)
                if risk > 0:
                    rr_list.append(reward / risk)
        avg_rr = float(np.mean(rr_list)) if rr_list else 1.5

        # Profit factor
        gross_profit = sum(wins) if wins else 0.0
        gross_loss   = abs(sum(losses)) if losses else 1e-9
        profit_factor = gross_profit / gross_loss

        return {
            "win_rate":      win_rate,
            "sharpe":        sharpe,
            "max_drawdown":  max_dd,
            "avg_rr":        avg_rr,
            "profit_factor": profit_factor,
        }

    # ── Directive generation ──────────────────────────────────────────────────
    def _generate_directive(self, metrics: dict, scores: dict) -> dict:
        """
        Computes parameter adjustments to steer the bot toward targets.
        Directive is written to logs/tune_directive.json which
        live_paper_trade.py reads on each tick.
        """
        directive: dict = {
            "risk_pct":     0.015,   # default
            "sl_atr_mult":  1.5,
            "tp_atr_mult":  3.0,
            "cooldown_sec": 30,
            "max_positions": 1,
        }

        wr    = metrics["win_rate"]
        dd    = metrics["max_drawdown"]
        pf    = metrics["profit_factor"]
        sh    = metrics["sharpe"]

        # Win rate too low → tighten entry (increase confidence threshold via cooldown)
        if wr < 0.45:
            directive["cooldown_sec"] = 120
            directive["risk_pct"]     = 0.010   # reduce risk
        elif wr >= 0.60:
            directive["cooldown_sec"] = 15
            directive["risk_pct"]     = 0.020   # increase risk on strong performance

        # Drawdown too high → reduce risk, widen SL
        if dd > 0.04:
            directive["risk_pct"]    = min(directive["risk_pct"], 0.008)
            directive["sl_atr_mult"] = 2.0    # wider SL to avoid early stops
        elif dd < 0.01:
            directive["sl_atr_mult"] = 1.2   # tighter SL when equity stable

        # Profit factor > 2 → allow 2 concurrent positions
        if pf > 2.0 and wr >= 0.55:
            directive["max_positions"] = 2

        # Write directive to disk
        directive["updated_at"] = datetime.utcnow().isoformat()
        TUNE_FILE.write_text(json.dumps(directive, indent=2))
        return directive

    # ── Persistence ───────────────────────────────────────────────────────────
    def _load_perf_history(self) -> List[dict]:
        if PERF_LOG.exists():
            try:
                return json.loads(PERF_LOG.read_text())
            except Exception:
                pass
        return []

    def _save(self, report: dict):
        self._perf_history = self._perf_history[-200:]   # keep last 200
        PERF_LOG.write_text(json.dumps(self._perf_history, indent=2))
        TARGETS_FILE.write_text(json.dumps(report, indent=2))


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = Autopilot()
    fake_trades = [
        {"realized_pnl": 12.0, "entry_price": 2000, "exit_price": 2012,
         "sl": 1990, "tp": 2024, "direction": "BUY"},
        {"realized_pnl": -5.0, "entry_price": 2010, "exit_price": 2005,
         "sl": 2020, "tp": 1990, "direction": "SELL"},
        {"realized_pnl":  8.0, "entry_price": 2015, "exit_price": 2023,
         "sl": 2005, "tp": 2031, "direction": "BUY"},
        {"realized_pnl": -3.0, "entry_price": 2020, "exit_price": 2017,
         "sl": 2030, "tp": 2008, "direction": "SELL"},
        {"realized_pnl": 15.0, "entry_price": 2018, "exit_price": 2033,
         "sl": 2008, "tp": 2038, "direction": "BUY"},
    ]
    result = ap.evaluate(fake_trades, {"equity": 10027, "balance": 10027})
    if result:
        print(json.dumps(result, indent=2))
