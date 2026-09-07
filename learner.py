"""
learner.py — Closed-loop online learner.
Learns from every closed trade outcome and updates ensemble weights
(gradient-boosted regret-matching with diversity floors).
Runs entirely in-process — no external API, no paid service.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

WEIGHTS_FILE = Path("logs/learner_weights.json")
WEIGHTS_FILE.parent.mkdir(exist_ok=True)

MODELS = ["ppo", "trend", "mean_rev", "momentum", "macro", "google_ai"]
MIN_W  = 0.08   # floor: 8 % each
MAX_W  = 0.45   # ceiling: 45 % each


def _uniform() -> Dict[str, float]:
    w = 1.0 / len(MODELS)
    return {m: w for m in MODELS}


def _clamp_normalize(raw: Dict[str, float]) -> Dict[str, float]:
    clamped = {k: max(MIN_W, min(MAX_W, v)) for k, v in raw.items()}
    total = sum(clamped.values())
    return {k: v / total for k, v in clamped.items()}


class ClosedLoopLearner:
    """
    After each closed trade:
      1. Compute a scalar reward from PnL / return %.
      2. Update each model's regret score (boost correct, decay wrong).
      3. Renormalize weights (with diversity floor/ceiling).
      4. Persist weights to disk so they survive restarts.
    """

    def __init__(self):
        self.iterations = 0
        self.weights: Dict[str, float] = self._load_weights()
        self._regret: Dict[str, float] = {m: self.weights.get(m, 1.0 / len(MODELS)) for m in MODELS}

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load_weights(self) -> Dict[str, float]:
        if WEIGHTS_FILE.exists():
            try:
                saved = json.loads(WEIGHTS_FILE.read_text())
                w = {m: float(saved.get(m, 1 / len(MODELS))) for m in MODELS}
                return _clamp_normalize(w)
            except Exception:
                pass
        return _uniform()

    def _save_weights(self):
        WEIGHTS_FILE.write_text(json.dumps(self.weights, indent=2))

    # ── Learning step ─────────────────────────────────────────────────────────
    def on_trade_closed(self, trade: dict) -> Dict[str, float]:
        """
        Called by the bot after every SL/TP or manual close.
        Returns updated weight dict.
        """
        pnl     = float(trade.get("realized_pnl", 0.0))
        ret_pct = float(trade.get("return_pct", pnl / 100.0))
        regime  = str(trade.get("regime", "DRL_PPO")).upper()

        # Map regime → which "model" gets credit/blame
        regime_model_map = {
            "DRL_PPO":        "ppo",
            "ML_POLICY":      "ppo",
            "TREND":          "trend",
            "QUANT_TREND":    "trend",
            "MEAN_REV":       "mean_rev",
            "QUANT_MEAN_REV": "mean_rev",
            "MOMENTUM":       "momentum",
            "QUANT_MOMENTUM": "momentum",
            "MACRO":          "macro",
            "QUANT_MACRO":    "macro",
            "GOOGLE_AI":      "google_ai",
            "GOOGLE_QUANT":   "google_ai",
            "GOOGLE_TREND":   "google_ai",
            "GOOGLE_REVERSAL":"google_ai",
        }
        primary = regime_model_map.get(regime, "trend")

        market_went_up = (trade.get("direction") == "BUY" and pnl > 0) or \
                         (trade.get("direction") == "SELL" and pnl < 0)

        for m in MODELS:
            # Simplified: primary model gets full credit, others get partial
            is_primary = (m == primary)
            weight = 1.0 if is_primary else 0.3

            if market_went_up:
                # Reward: boost by 5 % + |return| × 10
                self._regret[m] = max(
                    MIN_W, self._regret[m] * (1.0 + 0.05 * weight) + abs(ret_pct) * 10 * weight
                )
            else:
                # Penalty: decay by 8 %
                self._regret[m] = max(MIN_W, self._regret[m] * (1.0 - 0.08 * weight))

        self.weights = _clamp_normalize(self._regret)
        self.iterations += 1
        self._save_weights()

        logger.info(f"[Learner] iter={self.iterations}  PnL={pnl:+.2f}  "
                    f"regime={regime}  weights={self.weights}")
        return self.weights

    def reset(self):
        self.weights = _uniform()
        self._regret = dict(self.weights)
        self.iterations = 0
        self._save_weights()
