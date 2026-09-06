"""
live_paper_trade.py — Paper trading runner (replaces live_trade_mt5.py).

Uses:
  - yfinance for free live XAUUSD price ticks (no API key)
  - Pretrained PPO model from stable-baselines3
  - PaperBroker for virtual order execution with SL/TP
  - ClosedLoopLearner to self-improve from each closed trade

Run:
    python live_paper_trade.py

Press Ctrl+C to stop cleanly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

# ── Project imports ──────────────────────────────────────────────────────────
from live_feed import LiveGoldFeed, fetch_ohlcv
from paper_broker import PaperBroker
from features.make_features import compute_features
from learner import ClosedLoopLearner
from knowledge_db import KnowledgeDatabase

# ── Logging ──────────────────────────────────────────────────────────────────
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
WINDOW           = 64
SYMBOL           = "XAUUSD"
POLL_INTERVAL    = float(os.getenv("LIVE_POLL_SECONDS", "10"))
STATE_FILE       = Path("logs/paper_state.json")
STATE_FILE.parent.mkdir(exist_ok=True)


class PaperTradingBot:
    def __init__(self):
        self.broker  = PaperBroker(initial_balance=INITIAL_BALANCE)
        self.learner = ClosedLoopLearner()
        self.db      = KnowledgeDatabase()
        self.feed    = LiveGoldFeed(poll_interval=POLL_INTERVAL)
        self.model   = self._load_model()

        # Rolling bar buffer for feature computation (up to 500 bars)
        self.bar_buffer: pd.DataFrame = pd.DataFrame()
        self._load_history()

        self.is_active      = True
        self.tick_count     = 0
        self.last_action    = 0    # 0=Flat, 1=Long, 2=Short
        self.circuit_tripped = False

        self.feed.register_callback(self._on_tick)

    # ── Startup ──────────────────────────────────────────────────────────────
    def _load_model(self):
        mp = Path(MODEL_PATH)
        if not mp.exists():
            logger.warning(f"[Bot] No model at {mp} — running RANDOM policy")
            return None
        try:
            from stable_baselines3 import PPO
            model = PPO.load(str(mp))
            logger.info(f"[Bot] ✅ Model loaded: {mp}")
            return model
        except Exception as e:
            logger.error(f"[Bot] Model load failed: {e} — running RANDOM policy")
            return None

    def _load_history(self):
        """Pre-load historical bars so we have enough data for features."""
        try:
            logger.info("[Bot] Downloading historical OHLCV for feature warmup…")
            df = fetch_ohlcv("GC=F", period="3mo", interval="1h")
            self.bar_buffer = df.tail(500).copy()
            logger.info(f"[Bot] History loaded: {len(self.bar_buffer)} bars "
                        f"({self.bar_buffer['time'].iloc[0]} → {self.bar_buffer['time'].iloc[-1]})")
        except Exception as e:
            logger.error(f"[Bot] History load failed: {e}")

    # ── Tick handler ─────────────────────────────────────────────────────────
    async def _on_tick(self, tick: dict):
        price = tick["last"]
        self.tick_count += 1

        # 0. Process dashboard control commands
        self._process_command_queue(price)

        if not self.is_active:
            self._save_state(price)
            return

        # 1. SL / TP check & closed-trade feedback
        closed = self.broker.update_price(price)
        for trade in closed:
            learned = self.learner.on_trade_closed(trade)
            atr = self._calc_atr(14)
            lessons = self.db.log_trade_closed(trade, {"atr": atr, "rsi": 50.0})
            logger.info(f"[Learn+DB] Trade {trade['trade_id']} closed | PnL={trade['realized_pnl']:+.2f} | Lessons: {lessons}")

        # 2. Circuit-breaker: max daily drawdown
        dd = (self.broker.daily_loss_start - self.broker.equity) / self.broker.daily_loss_start
        if dd > MAX_DD_PCT:
            if not self.circuit_tripped:
                logger.warning(f"[CIRCUIT] Daily drawdown {dd:.1%} > {MAX_DD_PCT:.1%} — flattening all")
                self.broker.close_all(price, "CIRCUIT_BREAKER")
                self.circuit_tripped = True
            self._save_state(price)
            return

        # 3. Update bar buffer with synthetic tick-level bar
        self._update_bar_buffer(tick)

        # 4. Generate AI signal (every tick)
        action = self._predict_action(price)

        # 5. Execute if signal changed
        if len(self.broker.positions) == 0 and action != 0:
            self._enter_position(action, price)
        elif len(self.broker.positions) > 0 and action == 0:
            for tid in list(self.broker.positions):
                self.broker.close_position(tid, price, "MODEL_EXIT")

        # 6. Persist state
        self._save_state(price)

        # Log every ~30 ticks
        if self.tick_count % 30 == 0:
            s = self.broker.summary()
            logger.info(f"[Status] equity={s['equity']:.2f}  "
                        f"realized={s['realized_pnl']:+.2f}  "
                        f"open={s['open_positions']}  "
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
                    self._enter_position(act, price)
                    logger.info(f"[Command] Manual trade executed -> {dir_name} @ ${price:.2f}")
                elif cmd == "force_eval":
                    from autopilot import Autopilot
                    ap = Autopilot()
                    ap.evaluate(self.broker.closed_trades, self.broker.summary())
                    logger.info("[Command] Forced Autopilot evaluation completed")
        except Exception as e:
            logger.error(f"[Command] Error processing command queue: {e}")

    # ── Feature → Action ─────────────────────────────────────────────────────
    def _update_bar_buffer(self, tick: dict):
        """Append a synthetic 1-tick bar to the buffer."""
        price = tick["last"]
        now   = pd.Timestamp.now('UTC').tz_localize(None)
        row   = pd.DataFrame([{
            "time": now, "open": price, "high": price,
            "low": price, "close": price, "volume": 1.0
        }])
        self.bar_buffer = pd.concat([self.bar_buffer, row], ignore_index=True).tail(500)

    def _predict_action(self, price: float) -> int:
        """Return 0=Flat, 1=Long, 2=Short."""
        if self.model is None or len(self.bar_buffer) < WINDOW + 50:
            # Not enough data — random exploration
            return int(np.random.choice([0, 1], p=[0.6, 0.4]))

        try:
            _, feats, _ = compute_features(self.bar_buffer.copy())
            if feats is None or len(feats) < WINDOW:
                return 0

            obs_features = feats[-WINDOW:].astype(np.float32)
            current_pos  = 1 if len(self.broker.positions) > 0 else 0
            obs = np.concatenate([obs_features.reshape(-1),
                                  np.array([current_pos], dtype=np.float32)])

            action, _ = self.model.predict(obs, deterministic=True)
            return int(action)
        except Exception as e:
            logger.debug(f"[Bot] Predict error: {e}")
            return 0

    # ── Position sizing & entry ───────────────────────────────────────────────
    def _read_tune_directive() -> dict:
        tune_file = Path("logs/tune_directive.json")
        if tune_file.exists():
            try:
                return json.loads(tune_file.read_text())
            except Exception:
                pass
        return {}

    def _enter_position(self, action: int, price: float):
        direction = "BUY" if action == 1 else "SELL"
        directive = self._read_tune_directive()

        risk_pct    = float(directive.get("risk_pct", RISK_PCT))
        sl_mult     = float(directive.get("sl_atr_mult", 1.5))
        tp_mult     = float(directive.get("tp_atr_mult", 3.0))
        max_pos     = int(directive.get("max_positions", 1))

        if len(self.broker.positions) >= max_pos:
            return

        # ATR-based SL/TP from last 14 bars
        atr = self._calc_atr(14)
        sl_dist = max(atr * sl_mult, 3.0)
        tp_dist = max(atr * tp_mult, sl_dist * 1.5)

        # Knowledge Base Pattern Intelligence Filter
        db_mod, rationale = self.db.query_setup_intelligence({"direction": direction, "atr": atr, "rsi": 50.0})
        logger.info(f"[KnowledgeBase] {rationale} (size multiplier: {db_mod:.2f}x)")

        if db_mod < 0.65:
            logger.warning(f"[KnowledgeBase] Trade skipped due to low historical pattern confidence ({rationale})")
            return

        sl = price - sl_dist if direction == "BUY" else price + sl_dist
        tp = price + tp_dist if direction == "BUY" else price - tp_dist

        # Kelly-ish position sizing scaled by Knowledge Base intelligence multiplier
        risk_dollars = self.broker.equity * risk_pct * db_mod
        size         = round(risk_dollars / (sl_dist * 100), 2)   # in oz
        size         = max(0.01, min(size, 5.0))                   # clamp

        pos = self.broker.open_position(
            symbol=SYMBOL, direction=direction,
            price=price, size=size, sl=sl, tp=tp,
            confidence=0.75 * db_mod, regime="DRL_PPO"
        )
        if pos:
            self.last_action = action
            self.db.log_trade_opened(pos, {"atr": atr, "rsi": 50.0, "db_mod": db_mod})

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
    def _save_state(self, price: float):
        s = self.broker.summary()
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
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)

    # ── Main run loop ────────────────────────────────────────────────────────
    async def run(self):
        logger.info("=" * 60)
        logger.info("   Gold AI Paper Trading Bot — DRL Edition")
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
