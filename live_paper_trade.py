"""
live_paper_trade.py — High-Performance Gold AI Paper Trading Bot 2.0.

Features:
  - Free real-time live XAUUSD tick feed via yfinance
  - Quantitative Multi-Strategy Ensemble (Trend, Mean Reversion, Momentum, Macro, ML)
  - ClosedLoopLearner self-improvement driven by real strategy weights
  - Strict Trade Cooldown to eliminate overtrading & fee churn
  - Dynamic Breakeven & Trailing Stop loss protection
  - Persistent SQLite Knowledge Base & Pattern Memory
  - Synchronized real-time tuning directives from the Dashboard UI

Run:
    venv/bin/python live_paper_trade.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# ── Project imports ──────────────────────────────────────────────────────────
from live_feed import LiveGoldFeed, fetch_ohlcv
from paper_broker import PaperBroker
from learner import ClosedLoopLearner
from knowledge_db import KnowledgeDatabase
from models.quantitative_ensemble import QuantitativeEnsemble
from grid_engine import IntelligentGridEngine

# ── Logging ──────────────────────────────────────────────────────────────────
Path("logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/paper_trade.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
load_dotenv()
INITIAL_BALANCE  = float(os.getenv("INITIAL_BALANCE", "10000"))
RISK_PCT         = float(os.getenv("RISK_PER_TRADE_PCT", "0.015"))
MAX_DD_PCT       = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.03"))
MODEL_PATH       = os.getenv("MODEL_PATH", "train/ppo_xauusd_latest.zip")
SYMBOL           = "XAUUSD"
POLL_INTERVAL    = float(os.getenv("LIVE_POLL_SECONDS", "3.0"))
STATE_FILE       = Path("logs/paper_state.json")
TUNE_FILE        = Path("logs/tune_directive.json")
STATE_FILE.parent.mkdir(exist_ok=True)


class PaperTradingBot:
    def __init__(self):
        self.broker   = PaperBroker(initial_balance=INITIAL_BALANCE)
        self.learner  = ClosedLoopLearner()
        self.db       = KnowledgeDatabase()
        self.feed     = LiveGoldFeed(poll_interval=POLL_INTERVAL)
        self.ensemble = QuantitativeEnsemble()
        self.grid_engine = IntelligentGridEngine(symbol=SYMBOL)

        # Rolling bar buffer for indicator computation (up to 500 bars)
        self.bar_buffer: pd.DataFrame = pd.DataFrame()
        self._load_history()

        self.is_active           = True
        self.tick_count          = 0
        self.last_action         = 0    # 0=Flat, 1=Long, 2=Short
        self.circuit_tripped     = False
        self.last_trade_closed_ts = 0.0
        self.last_ensemble_res   = {}

        self.feed.register_callback(self._on_tick)

    # ── Startup ──────────────────────────────────────────────────────────────
    def _load_history(self):
        """Pre-load historical bars so we have enough data for indicators & ML."""
        try:
            logger.info("[Bot] Downloading historical OHLCV for quant indicator warmup…")
            df = fetch_ohlcv("GC=F", period="3mo", interval="1h")
            self.bar_buffer = df.tail(500).copy()
            logger.info(f"[Bot] History loaded: {len(self.bar_buffer)} bars "
                        f"({self.bar_buffer['time'].iloc[0]} → {self.bar_buffer['time'].iloc[-1]})")
            # Calibrate ML classifier immediately
            self.ensemble._train_ml_model(self.bar_buffer)
            # Bootstrap Knowledge Database with factual historical setups
            self.db.bootstrap_from_historical_data(self.bar_buffer)
        except Exception as e:
            logger.error(f"[Bot] History load failed: {e}")

    # ── Directive & Cooldown Helper ──────────────────────────────────────────
    def _read_tune_directive(self) -> dict:
        if TUNE_FILE.exists():
            try:
                return json.loads(TUNE_FILE.read_text())
            except Exception:
                pass
        return {
            "risk_pct": RISK_PCT,
            "sl_atr_mult": 1.0,
            "tp_atr_mult": 1.5,
            "cooldown_sec": 15,
            "max_positions": 2,
        }

    # ── Dynamic Breakeven & Trailing Stop ────────────────────────────────────
    def _manage_trailing_stops(self, current_price: float):
        """
        Locks in profits:
        - When trade hits +1R profit, move SL to breakeven + cushion.
        - When trade hits +2R profit, trail SL to lock in at least +1R.
        """
        for tid, pos in self.broker.positions.items():
            if pos.direction == "BUY":
                init_risk = max(pos.entry_price - pos.sl, 1.0)
                # +0.5R Breakeven lock (velocity acceleration)
                if current_price >= pos.entry_price + 0.5 * init_risk and pos.sl < pos.entry_price:
                    new_sl = round(pos.entry_price + 0.20, 2)
                    pos.sl = new_sl
                    logger.info(f"[Trailing Stop] BUY trade {tid} locked at BREAKEVEN (${new_sl:.2f})")
                # +1.0R Trail lock
                elif current_price >= pos.entry_price + 1.0 * init_risk:
                    new_sl = round(current_price - 0.6 * self._calc_atr(14), 2)
                    if new_sl > pos.sl:
                        pos.sl = new_sl
                        logger.info(f"[Trailing Stop] BUY trade {tid} trailing SL bumped to ${new_sl:.2f}")
            elif pos.direction == "SELL":
                init_risk = max(pos.sl - pos.entry_price, 1.0)
                # +0.5R Breakeven lock
                if current_price <= pos.entry_price - 0.5 * init_risk and pos.sl > pos.entry_price:
                    new_sl = round(pos.entry_price - 0.20, 2)
                    pos.sl = new_sl
                    logger.info(f"[Trailing Stop] SELL trade {tid} locked at BREAKEVEN (${new_sl:.2f})")
                # +1.0R Trail lock
                elif current_price <= pos.entry_price - 1.0 * init_risk:
                    new_sl = round(current_price + 0.6 * self._calc_atr(14), 2)
                    if new_sl < pos.sl:
                        pos.sl = new_sl
                        logger.info(f"[Trailing Stop] SELL trade {tid} trailing SL bumped to ${new_sl:.2f}")

    # ── Tick handler ─────────────────────────────────────────────────────────
    async def _on_tick(self, tick: dict):
        price = tick["last"]
        self.tick_count += 1
        directive = self._read_tune_directive()

        # 0. Process dashboard control commands
        self._process_command_queue(price)

        if not self.is_active:
            self._save_state(price, directive)
            return

        # 1. Update position prices & process SL / TP hits
        closed = self.broker.update_price(price)
        if closed:
            self.last_trade_closed_ts = time.time()
            for trade in closed:
                learned_weights = self.learner.on_trade_closed(trade)
                atr = self._calc_atr(14)
                lessons = self.db.log_trade_closed(trade, {"atr": atr, "rsi": 50.0})
                logger.info(f"[Learn+DB] Trade {trade['trade_id']} closed | PnL={trade['realized_pnl']:+.2f} | "
                            f"Reason={trade['exit_reason']} | Weights={learned_weights} | Lessons: {lessons}")

        # 2. Dynamic Trailing Stop & Breakeven Management
        self._manage_trailing_stops(price)

        # 3. Circuit-breaker: max daily drawdown
        dd = (self.broker.daily_loss_start - self.broker.equity) / self.broker.daily_loss_start
        if dd > MAX_DD_PCT:
            if not self.circuit_tripped:
                logger.warning(f"[CIRCUIT] Daily drawdown {dd:.1%} > {MAX_DD_PCT:.1%} — flattening all")
                self.broker.close_all(price, "CIRCUIT_BREAKER")
                self.circuit_tripped = True
                self.last_trade_closed_ts = time.time()
            self._save_state(price, directive)
            return

        # 4. Update bar buffer with synthetic tick-level bar
        self._update_bar_buffer(tick)

        # 5. Evaluate Multi-Strategy Quantitative Ensemble
        threshold = float(directive.get("consensus_threshold", 0.12))
        ens_res = self.ensemble.evaluate(self.bar_buffer, self.learner.weights, threshold=threshold)
        self.last_ensemble_res = ens_res
        action = ens_res["action"]  # 0=Flat, 1=Buy, 2=Sell

        # 6. Cooldown Check
        cooldown_sec = float(directive.get("cooldown_sec", 60.0))
        time_since_closed = time.time() - self.last_trade_closed_ts
        in_cooldown = time_since_closed < cooldown_sec

        # 7. Position Execution Logic
        max_pos = int(directive.get("max_positions", 1))

        # Check for Strong Opposite Reversal (only exit existing position on high conviction opposite signal)
        if len(self.broker.positions) > 0:
            for tid, pos in list(self.broker.positions.items()):
                is_buy_reversal = (pos.direction == "BUY" and action == 2 and ens_res["confidence"] >= 0.65)
                is_sell_reversal = (pos.direction == "SELL" and action == 1 and ens_res["confidence"] >= 0.65)
                if is_buy_reversal or is_sell_reversal:
                    logger.info(f"[REVERSAL] Strong opposite signal detected ({ens_res['regime_lead']} {ens_res['direction']} @ {ens_res['confidence']:.2f}) — exiting {tid}")
                    self.broker.close_position(tid, price, "REVERSAL_EXIT")
                    self.last_trade_closed_ts = time.time()

        # Enter new position if flat, action != 0, and not in cooldown
        if len(self.broker.positions) < max_pos and action != 0:
            if in_cooldown:
                logger.debug(f"[Cooldown] Signal generated ({ens_res['direction']}) but cooling down ({time_since_closed:.0f}s/{cooldown_sec:.0f}s)")
            else:
                self._enter_position(action, price, directive, ens_res)

        # 8. Intelligent Quantitative Grid Trading Engine
        atr = self._calc_atr(14)
        kb_mod, _ = self.db.query_setup_intelligence({
            "direction": ens_res.get("direction", "BUY"),
            "atr": atr,
            "rsi": 50.0,
            "regime": ens_res.get("regime_lead", "QUANT")
        })
        self.grid_engine.update(price, atr, ens_res, kb_mod, self.broker, directive)

        # 9. Persist state
        self._save_state(price, directive)

        # Periodic status logging
        if self.tick_count % 30 == 0:
            s = self.broker.summary()
            logger.info(f"[Status] equity={s['equity']:.2f}  "
                        f"realized={s['realized_pnl']:+.2f}  "
                        f"open={s['open_positions']}  "
                        f"consensus={ens_res['consensus_score']:+.2f} ({ens_res['direction']})  "
                        f"ticks={self.tick_count}")

    # ── Command Queue Handling ───────────────────────────────────────────────
    def _process_command_queue(self, price: float):
        cmd_file = Path("data/command_queue.json")
        if not cmd_file.exists():
            return
        try:
            cmds = json.loads(cmd_file.read_text())
            if not isinstance(cmds, list):
                cmds = [cmds]
            cmd_file.unlink(missing_ok=True)
            for c in cmds:
                cmd = c.get("command")
                if cmd == "toggle_active":
                    self.is_active = not self.is_active
                    logger.info(f"[Command] Bot state toggled -> Active={self.is_active}")
                elif cmd == "manual_trade":
                    dir_name = c.get("direction", "BUY")
                    act = 1 if dir_name == "BUY" else 2
                    directive = self._read_tune_directive()
                    fake_ens = {
                        "confidence": 0.80,
                        "regime_lead": "MANUAL",
                        "consensus_score": 0.80 if act == 1 else -0.80,
                    }
                    self._enter_position(act, price, directive, fake_ens)
                    logger.info(f"[Command] Manual trade executed -> {dir_name} @ ${price:.2f}")
                elif cmd == "close_all":
                    self.broker.close_all(price, "MANUAL_FLATTEN")
                    self.last_trade_closed_ts = time.time()
                    logger.info(f"[Command] Manual close all positions executed @ ${price:.2f}")
                elif cmd == "force_eval":
                    from autopilot import Autopilot
                    ap = Autopilot()
                    ap.evaluate(self.broker.closed_trades, self.broker.summary())
                    logger.info("[Command] Forced Autopilot evaluation completed")
        except Exception as e:
            logger.error(f"[Command] Error processing command queue: {e}")

    # ── Bar Buffer Update ────────────────────────────────────────────────────
    def _update_bar_buffer(self, tick: dict):
        """Update active bar with live tick price, or append a new bar when 15m rolls over."""
        price = float(tick["last"])
        if len(self.bar_buffer) == 0:
            now = pd.Timestamp.now('UTC').tz_localize(None)
            self.bar_buffer = pd.DataFrame([{
                "time": now, "open": price, "high": price,
                "low": price, "close": price, "volume": 1.0
            }])
            return

        last_idx = self.bar_buffer.index[-1]
        last_time = pd.to_datetime(self.bar_buffer.loc[last_idx, "time"])
        now = pd.Timestamp.now('UTC').tz_localize(None)

        # If more than 15 minutes have passed since last bar, start a new bar
        if (now - last_time).total_seconds() >= 900:
            new_bar = pd.DataFrame([{
                "time": now, "open": price, "high": price,
                "low": price, "close": price, "volume": 1.0
            }])
            self.bar_buffer = pd.concat([self.bar_buffer, new_bar], ignore_index=True).tail(500)
        else:
            # Update current active bar
            self.bar_buffer.loc[last_idx, "close"] = price
            self.bar_buffer.loc[last_idx, "high"] = max(float(self.bar_buffer.loc[last_idx, "high"]), price)
            self.bar_buffer.loc[last_idx, "low"] = min(float(self.bar_buffer.loc[last_idx, "low"]), price)

    # ── Position Sizing & Entry ───────────────────────────────────────────────
    def _enter_position(self, action: int, price: float, directive: dict, ens_res: dict):
        direction = "BUY" if action == 1 else "SELL"

        risk_pct = float(directive.get("risk_pct", RISK_PCT))
        sl_mult  = float(directive.get("sl_atr_mult", 1.5))
        tp_mult  = float(directive.get("tp_atr_mult", 3.0))

        # ATR-based SL/TP from last 14 bars
        atr = self._calc_atr(14)
        sl_dist = max(atr * sl_mult, 3.0)
        tp_dist = max(atr * tp_mult, sl_dist * 1.5)

        # Knowledge Base Pattern Intelligence Filter
        db_mod, rationale = self.db.query_setup_intelligence({
            "direction": direction,
            "atr": atr,
            "rsi": 50.0,
            "regime": ens_res.get("regime_lead", "QUANT")
        })
        logger.info(f"[KnowledgeBase] {rationale} (size multiplier: {db_mod:.2f}x)")

        if db_mod < 0.60:
            logger.warning(f"[KnowledgeBase] Trade skipped due to low historical setup confidence ({rationale})")
            return

        sl = price - sl_dist if direction == "BUY" else price + sl_dist
        tp = price + tp_dist if direction == "BUY" else price - tp_dist

        # Position Sizing: Risk % * db_mod
        risk_dollars = self.broker.equity * risk_pct * db_mod
        size         = round(risk_dollars / sl_dist, 2)          # in oz (realistic position sizing)
        size         = max(0.10, min(size, 5.0))                 # clamp between 0.10 oz and 5.0 oz

        regime = f"QUANT_{ens_res.get('regime_lead', 'ENS')}"
        conf = ens_res.get("confidence", 0.75) * db_mod

        pos = self.broker.open_position(
            symbol=SYMBOL, direction=direction,
            price=price, size=size, sl=sl, tp=tp,
            confidence=conf, regime=regime
        )
        if pos:
            self.last_action = action
            self.db.log_trade_opened(pos, {
                "atr": atr,
                "rsi": 50.0,
                "consensus": ens_res.get("consensus_score", 0.0),
                "regime_lead": ens_res.get("regime_lead", "QUANT"),
                "db_mod": db_mod
            })

    def _calc_atr(self, period: int = 14) -> float:
        df = self.bar_buffer.tail(period + 1)
        if len(df) < 2:
            return 5.0
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        trs = [highs[0] - lows[0]]
        for i in range(1, len(df)):
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i-1]),
                           abs(lows[i]  - closes[i-1])))
        return float(np.mean(trs[-period:]))

    # ── State persistence ────────────────────────────────────────────────────
    def _save_state(self, price: float, directive: dict):
        s = self.broker.summary()
        cooldown_sec = float(directive.get("cooldown_sec", 60.0))
        elapsed = time.time() - self.last_trade_closed_ts
        cd_rem = max(0, int(cooldown_sec - elapsed)) if self.last_trade_closed_ts > 0 else 0

        atr = self._calc_atr(14)
        closes = self.bar_buffer["close"].to_numpy(dtype=float) if len(self.bar_buffer) > 0 else np.array([price])
        rsi_val = 50.0
        if len(closes) >= 15:
            delta = pd.Series(closes).diff()
            gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rs = gain / (loss + 1e-9)
            rsi_val = float(100 - (100 / (1 + rs)))

        kb_mod, kb_rat = self.db.query_setup_intelligence({
            "direction": self.last_ensemble_res.get("direction", "BUY"),
            "atr": atr,
            "rsi": rsi_val,
        })

        state = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "spot_price":  round(price, 2),
            "is_active":   self.is_active,
            "circuit_breaker": self.circuit_tripped,
            "account":     s,
            "positions":   [p.to_dict() for p in self.broker.positions.values()],
            "recent_trades": self.broker.closed_trades[-20:],
            "learner_iterations": self.learner.iterations,
            "learner_weights":    self.learner.weights,
            "tick_count":  self.tick_count,
            "ensemble_signals":   self.last_ensemble_res.get("breakdown", {}),
            "consensus_score":    self.last_ensemble_res.get("consensus_score", 0.0),
            "consensus_direction": self.last_ensemble_res.get("direction", "FLAT"),
            "consensus_confidence": self.last_ensemble_res.get("confidence", 0.0),
            "uncertainty":        self.last_ensemble_res.get("uncertainty", 0.0),
            "lead_regime":        self.last_ensemble_res.get("regime_lead", "QUANT"),
            "cooldown_remaining": cd_rem,
            "google_decision":    self.last_ensemble_res.get("google_decision", {}),
            "factual_indicators": {
                "atr": round(atr, 2),
                "rsi": round(rsi_val, 1),
                "day_high": round(float(self.bar_buffer["high"].max()), 2) if len(self.bar_buffer) > 0 else round(price, 2),
                "day_low": round(float(self.bar_buffer["low"].min()), 2) if len(self.bar_buffer) > 0 else round(price, 2),
                "spread": 0.15,
                "velocity_mode": "FAST_QUANT (3.0s)",
            },
            "kb_intelligence": {
                "multiplier": kb_mod,
                "rationale": kb_rat,
            },
            "grid_state": self.grid_engine.get_grid_state(),
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)

    # ── Main run loop ────────────────────────────────────────────────────────
    async def run(self):
        logger.info("=" * 60)
        logger.info("   ⚡ Gold AI Paper Trading Bot 2.0 — Quantitative Ensemble")
        logger.info(f"   Balance: ${INITIAL_BALANCE:,.2f}  Risk/trade: {RISK_PCT:.1%}")
        logger.info(f"   Poll interval: {POLL_INTERVAL}s")
        logger.info("=" * 60)
        await self.feed.start()


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    bot = PaperTradingBot()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig, frame):
        logger.info(f"\n[Bot] Caught {signal.Signals(sig).name} — shutting down cleanly")
        bot.feed.stop()
        loop.stop()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(bot.run())
    finally:
        loop.close()
        logger.info("[Bot] Stopped.")


if __name__ == "__main__":
    main()
