"""
paper_broker.py — Paper trading broker (no MetaTrader5 required).
Simulates order execution, SL/TP, position tracking, and PnL
using live yfinance price feeds.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COMMISSION_PER_UNIT = 0.05   # $/oz equivalent flat fee
DEFAULT_SLIPPAGE    = 0.10   # $0.10 slippage per fill


class Position:
    def __init__(self, trade_id: str, symbol: str, direction: str,
                 entry_price: float, size: float, sl: float, tp: float,
                 confidence: float = 0.7, regime: str = "DRL"):
        self.trade_id    = trade_id
        self.symbol      = symbol
        self.direction   = direction          # "BUY" | "SELL"
        self.entry_price = entry_price
        self.size        = size
        self.sl          = sl
        self.tp          = tp
        self.confidence  = confidence
        self.regime      = regime
        self.entry_time  = datetime.utcnow().isoformat()
        self.current_price = entry_price
        self.unrealized_pnl = 0.0
        self.status      = "OPEN"

    def mark_to_market(self, price: float) -> float:
        self.current_price = price
        if self.direction == "BUY":
            self.unrealized_pnl = (price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.size
        return self.unrealized_pnl

    def to_dict(self) -> dict:
        return {
            "trade_id":      self.trade_id,
            "symbol":        self.symbol,
            "direction":     self.direction,
            "entry_price":   round(self.entry_price, 2),
            "current_price": round(self.current_price, 2),
            "size":          self.size,
            "sl":            round(self.sl, 2),
            "tp":            round(self.tp, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "confidence":    self.confidence,
            "regime":        self.regime,
            "entry_time":    self.entry_time,
            "status":        self.status,
        }


class PaperBroker:
    """
    Fully in-memory paper broker.
    Thread-safe for single-threaded async loops.
    """

    def __init__(self, initial_balance: float = 10_000.0):
        self.balance        = initial_balance
        self.equity         = initial_balance
        self.unrealized_pnl = 0.0
        self.realized_pnl   = 0.0
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[dict]      = []
        self.daily_loss_start = initial_balance

    # ------------------------------------------------------------------ #
    #  Core tick update                                                    #
    # ------------------------------------------------------------------ #
    def update_price(self, price: float) -> List[dict]:
        """
        Called on every live tick.  Marks positions to market, fires SL/TP.
        Returns list of just-closed trade dicts (for feedback to learner).
        """
        closed: List[dict] = []
        for tid in list(self.positions):
            pos = self.positions[tid]
            pos.mark_to_market(price)

            hit_sl = (pos.direction == "BUY"  and price <= pos.sl) or \
                     (pos.direction == "SELL" and price >= pos.sl)
            hit_tp = (pos.direction == "BUY"  and price >= pos.tp) or \
                     (pos.direction == "SELL" and price <= pos.tp)

            if hit_sl:
                ct = self._close(tid, pos.sl, "STOP_LOSS")
                closed.append(ct)
            elif hit_tp:
                ct = self._close(tid, pos.tp, "TAKE_PROFIT")
                closed.append(ct)

        # Recompute equity
        self.unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        self.equity = self.balance + self.unrealized_pnl
        return closed

    # ------------------------------------------------------------------ #
    #  Orders                                                              #
    # ------------------------------------------------------------------ #
    def open_position(self, symbol: str, direction: str, price: float,
                      size: float, sl: float, tp: float,
                      confidence: float = 0.7, regime: str = "DRL") -> Optional[dict]:
        if size <= 0:
            return None

        slip = DEFAULT_SLIPPAGE if direction == "BUY" else -DEFAULT_SLIPPAGE
        fill_price = price + slip
        fee = size * COMMISSION_PER_UNIT
        self.balance -= fee

        tid = f"XAU_{uuid.uuid4().hex[:8].upper()}"
        pos = Position(tid, symbol, direction, fill_price, size, sl, tp, confidence, regime)
        self.positions[tid] = pos

        logger.info(f"[PAPER OPEN] {direction} {size:.2f}oz @ {fill_price:.2f}  "
                    f"SL={sl:.2f}  TP={tp:.2f}  id={tid}")
        return pos.to_dict()

    def close_position(self, trade_id: str, price: float,
                       reason: str = "MANUAL") -> Optional[dict]:
        if trade_id not in self.positions:
            return None
        return self._close(trade_id, price, reason)

    def close_all(self, price: float, reason: str = "FLATTEN") -> List[dict]:
        return [self._close(tid, price, reason) for tid in list(self.positions)]

    # ------------------------------------------------------------------ #
    #  Internal                                                            #
    # ------------------------------------------------------------------ #
    def _close(self, trade_id: str, exit_price: float, reason: str) -> dict:
        pos = self.positions.pop(trade_id)
        if pos.direction == "BUY":
            pnl = (exit_price - pos.entry_price) * pos.size
        else:
            pnl = (pos.entry_price - exit_price) * pos.size

        fee = pos.size * COMMISSION_PER_UNIT
        net = pnl - fee

        self.balance += net
        self.realized_pnl += net

        trade = {
            **pos.to_dict(),
            "exit_price":   round(exit_price, 2),
            "exit_time":    datetime.utcnow().isoformat(),
            "realized_pnl": round(net, 4),
            "exit_reason":  reason,
            "status":       "CLOSED",
        }
        self.closed_trades.append(trade)
        logger.info(f"[PAPER CLOSE] {reason} {pos.direction} {pos.size:.2f}oz @ "
                    f"{exit_price:.2f}  PnL={net:+.2f}  id={trade_id}")
        return trade

    # ------------------------------------------------------------------ #
    #  Summary                                                             #
    # ------------------------------------------------------------------ #
    def summary(self) -> dict:
        return {
            "balance":        round(self.balance, 2),
            "equity":         round(self.equity, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "realized_pnl":   round(self.realized_pnl, 2),
            "open_positions": len(self.positions),
            "closed_trades":  len(self.closed_trades),
        }
