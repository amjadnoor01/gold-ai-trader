"""
grid_engine.py — Intelligent Quantitative Grid Trading Engine for Gold (XAUUSD)

Integrates:
1. Dynamic Regime Adaptability (Bilateral Neutral vs Trend-Biased)
2. ATR-Calibrated Rung Geometry (Vol-adjusted spacing)
3. Knowledge Base Expectancy Sizing
4. Microstructure Anti-Trap Risk Controls & Automatic Re-centering
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class GridRung:
    """Represents a single level in the quantitative grid."""

    def __init__(self, level_index: int, rung_type: str, price: float,
                 size: float, tp: float, sl: float):
        self.level_index = level_index   # -N .. +N relative to center (0)
        self.rung_type   = rung_type     # "BUY" | "SELL"
        self.price       = round(price, 2)
        self.size        = round(size, 2)
        self.tp          = round(tp, 2)
        self.sl          = round(sl, 2)
        self.status      = "PENDING"     # "PENDING" | "FILLED" | "HARVESTED"
        self.trade_id    = ""
        self.fill_time   = 0.0

    def to_dict(self) -> dict:
        return {
            "level":    self.level_index,
            "type":     self.rung_type,
            "price":    self.price,
            "size":     self.size,
            "tp":       self.tp,
            "sl":       self.sl,
            "status":   self.status,
            "trade_id": self.trade_id,
        }


class IntelligentGridEngine:
    """
    Intelligent Grid Engine that executes grid rungs based on bot consensus,
    volatility, and knowledge base expectancy.
    """

    def __init__(self, symbol: str = "XAUUSD"):
        self.symbol = symbol
        self.enabled = True
        self.mode = "AUTO"             # "AUTO" | "BILATERAL" | "TREND_BIASED" | "OFF"
        self.active_regime = "STANDBY" # "BILATERAL_NEUTRAL" | "TREND_BIASED_BULL" | "TREND_BIASED_BEAR" | "RECENTERING"
        
        self.center_price = 0.0
        self.spacing = 0.0
        self.upper_bound = 0.0
        self.lower_bound = 0.0
        self.rungs: List[GridRung] = []
        
        self.total_harvested_pnl = 0.0
        self.total_grid_trades = 0
        self.grid_wins = 0
        self.last_recenter_ts = 0.0
        self.recenter_cooldown = 120.0  # seconds between auto-recenterings unless out of bounds

    def configure(self, directive: dict):
        """Update runtime configuration from directive."""
        self.enabled = directive.get("grid_enabled", True)
        self.mode = directive.get("grid_mode", "AUTO")

    def update(self, spot_price: float, atr: float, ensemble_res: dict,
               kb_mod: float, broker, directive: dict) -> List[dict]:
        """
        Main tick loop entrypoint for Grid Trading.
        1. Checks existing grid positions against broker fills/TPs.
        2. Detects regime and decides whether to initialize or re-center grid.
        3. Monitors spot price crossing pending rungs and executes fills.
        """
        self.configure(directive)
        if not self.enabled or self.mode == "OFF":
            self.active_regime = "STANDBY"
            return []

        # Ensure valid ATR
        atr = max(atr, 4.0)

        # 1. Sync rungs with broker's actual open positions
        self._sync_with_broker(broker, spot_price)

        # 2. Check if grid needs initialization or re-centering
        spacing_mult = float(directive.get("grid_spacing_atr_mult", 0.30))
        num_levels = int(directive.get("grid_levels", 6)) // 2  # per side (e.g. 3 buy, 3 sell)
        num_levels = max(1, min(num_levels, 5))

        regime_lead = ensemble_res.get("regime_lead", "QUANT")
        consensus = ensemble_res.get("consensus_score", 0.0)

        needs_init = (self.center_price == 0.0 or len(self.rungs) == 0)
        out_of_bounds = False
        if not needs_init:
            out_of_bounds = (spot_price > self.upper_bound + (self.spacing * 0.5) or
                             spot_price < self.lower_bound - (self.spacing * 0.5))

        # Check regime alignment
        target_regime = self._determine_grid_regime(consensus, regime_lead)
        regime_shift = (self.active_regime not in ("STANDBY", "RECENTERING") and
                        self.active_regime != target_regime and
                        time.time() - self.last_recenter_ts > self.recenter_cooldown)

        if needs_init or out_of_bounds or regime_shift:
            self._deploy_new_grid(spot_price, atr, spacing_mult, num_levels,
                                  target_regime, kb_mod, broker, directive)

        # 3. Check for price crossing pending rungs
        self._check_rung_triggers(spot_price, broker, directive, ensemble_res, kb_mod)

        return []

    def _determine_grid_regime(self, consensus: float, regime_lead: str) -> str:
        """Determines whether to trade bilateral neutral or trend-biased."""
        if self.mode == "BILATERAL":
            return "BILATERAL_NEUTRAL"
        elif self.mode == "TREND_BIASED":
            return "TREND_BIASED_BULL" if consensus >= 0 else "TREND_BIASED_BEAR"

        # AUTO mode: AI-guided decision
        if abs(consensus) < 0.20 or regime_lead in ("MEAN_REV", "NEUTRAL"):
            return "BILATERAL_NEUTRAL"
        elif consensus >= 0.20 or (regime_lead in ("TREND", "MOMENTUM") and consensus > 0):
            return "TREND_BIASED_BULL"
        elif consensus <= -0.20 or (regime_lead in ("TREND", "MOMENTUM") and consensus < 0):
            return "TREND_BIASED_BEAR"
        return "BILATERAL_NEUTRAL"

    def _deploy_new_grid(self, spot_price: float, atr: float, spacing_mult: float,
                          num_levels: int, target_regime: str, kb_mod: float,
                          broker, directive: dict):
        """Constructs and deploys a fresh set of intelligent rungs around anchor."""
        # Only clear pending unfulfilled rungs
        self.rungs = [r for r in self.rungs if r.status == "FILLED"]

        self.center_price = round(spot_price, 2)
        self.spacing = round(max(atr * spacing_mult, 3.0), 2)
        self.active_regime = target_regime
        self.last_recenter_ts = time.time()

        # Risk budget per rung
        grid_risk_pct = float(directive.get("grid_max_risk_pct", 0.015))
        total_risk_dollars = broker.equity * grid_risk_pct * kb_mod
        total_rungs_active = num_levels * (2 if target_regime == "BILATERAL_NEUTRAL" else 1)
        risk_per_rung = total_risk_dollars / max(total_rungs_active, 1)
        rung_size = round(risk_per_rung / self.spacing, 2)
        rung_size = max(0.10, min(rung_size, 1.50))  # conservative sizing per rung (0.1 to 1.5 oz)

        new_rungs = []

        if target_regime in ("BILATERAL_NEUTRAL", "TREND_BIASED_BULL"):
            # Buy rungs below center price
            for i in range(1, num_levels + 1):
                p = self.center_price - (i * self.spacing)
                tp = p + self.spacing
                sl = self.center_price - ((num_levels + 1.5) * self.spacing)
                new_rungs.append(GridRung(
                    level_index=-i,
                    rung_type="BUY",
                    price=p,
                    size=rung_size,
                    tp=tp,
                    sl=sl
                ))

        if target_regime in ("BILATERAL_NEUTRAL", "TREND_BIASED_BEAR"):
            # Sell rungs above center price
            for i in range(1, num_levels + 1):
                p = self.center_price + (i * self.spacing)
                tp = p - self.spacing
                sl = self.center_price + ((num_levels + 1.5) * self.spacing)
                new_rungs.append(GridRung(
                    level_index=i,
                    rung_type="SELL",
                    price=p,
                    size=rung_size,
                    tp=tp,
                    sl=sl
                ))

        self.rungs.extend(new_rungs)
        self.rungs.sort(key=lambda r: r.price)

        all_prices = [r.price for r in self.rungs] + [self.center_price]
        self.upper_bound = max(all_prices)
        self.lower_bound = min(all_prices)

        logger.info(f"[GridEngine] Deployed {target_regime} grid at Anchor=${self.center_price:.2f} | "
                    f"Spacing=${self.spacing:.2f} ({spacing_mult:.2f}x ATR) | "
                    f"Range: [${self.lower_bound:.2f} - ${self.upper_bound:.2f}] | "
                    f"Rungs: {len(self.rungs)} | Size: {rung_size:.2f}oz/rung")

    def _check_rung_triggers(self, spot_price: float, broker, directive: dict,
                             ensemble_res: dict, kb_mod: float):
        """Checks if current spot price has touched/crossed any pending rungs."""
        for rung in self.rungs:
            if rung.status != "PENDING":
                continue

            triggered = False
            if rung.rung_type == "BUY" and spot_price <= rung.price:
                triggered = True
            elif rung.rung_type == "SELL" and spot_price >= rung.price:
                triggered = True

            if triggered:
                regime_tag = f"GRID_{rung.rung_type}_{self.active_regime[:4]}"
                pos = broker.open_position(
                    symbol=self.symbol,
                    direction=rung.rung_type,
                    price=spot_price,
                    size=rung.size,
                    sl=rung.sl,
                    tp=rung.tp,
                    confidence=0.80,
                    regime=regime_tag
                )
                if pos:
                    rung.status = "FILLED"
                    rung.trade_id = pos["trade_id"]
                    rung.fill_time = time.time()
                    logger.info(f"[GridEngine] Filled Level {rung.level_index:+d} {rung.rung_type} "
                                f"@ {spot_price:.2f} | Target TP=${rung.tp:.2f} | ID={pos['trade_id']}")

    def _sync_with_broker(self, broker, spot_price: float):
        """Tracks closed grid positions and accumulates harvested profit."""
        open_ids = set(broker.positions.keys())
        for rung in self.rungs:
            if rung.status == "FILLED" and rung.trade_id not in open_ids:
                rung.status = "HARVESTED"
                for ct in reversed(broker.closed_trades):
                    if ct.get("trade_id") == rung.trade_id:
                        pnl = float(ct.get("realized_pnl", 0.0))
                        self.total_harvested_pnl += pnl
                        self.total_grid_trades += 1
                        if pnl > 0:
                            self.grid_wins += 1
                        logger.info(f"[GridEngine] Rung {rung.level_index:+d} harvested | "
                                    f"PnL: {pnl:+.2f} | Cum Harvest: ${self.total_harvested_pnl:+.2f}")
                        break

    def get_grid_state(self) -> dict:
        """Returns structured JSON serialization for telemetry and dashboard."""
        active_rungs = [r for r in self.rungs if r.status in ("PENDING", "FILLED")]
        filled_rungs = [r for r in self.rungs if r.status == "FILLED"]
        active_exposure = round(sum(r.size for r in filled_rungs), 2)
        win_rate = round((self.grid_wins / self.total_grid_trades * 100), 1) if self.total_grid_trades > 0 else 0.0

        return {
            "enabled":              self.enabled,
            "mode":                 self.mode,
            "active_regime":        self.active_regime,
            "center_price":         self.center_price,
            "spacing":              self.spacing,
            "upper_bound":          self.upper_bound,
            "lower_bound":          self.lower_bound,
            "total_rungs":          len(self.rungs),
            "pending_rungs_count":  len([r for r in self.rungs if r.status == "PENDING"]),
            "filled_rungs_count":   len(filled_rungs),
            "active_exposure_oz":   active_exposure,
            "total_harvested_pnl":  round(self.total_harvested_pnl, 2),
            "total_grid_trades":    self.total_grid_trades,
            "grid_win_rate":        win_rate,
            "rungs":                [r.to_dict() for r in self.rungs]
        }
