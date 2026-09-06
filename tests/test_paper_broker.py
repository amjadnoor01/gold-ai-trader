"""
tests/test_paper_broker.py — Unit tests for paper_broker.PaperBroker
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from paper_broker import PaperBroker

SYMBOL = "XAUUSD"


@pytest.fixture
def broker():
    return PaperBroker(initial_balance=10_000.0)


# ────────────────────────────────────────────────────────────────────────────
class TestOpenPosition:
    def test_open_long(self, broker):
        pos = broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2020.0)
        assert pos is not None
        assert pos["direction"] == "BUY"
        assert len(broker.positions) == 1

    def test_open_short(self, broker):
        pos = broker.open_position(SYMBOL, "SELL", 2000.0, 1.0, sl=2010.0, tp=1980.0)
        assert pos is not None
        assert pos["direction"] == "SELL"

    def test_zero_size_rejected(self, broker):
        pos = broker.open_position(SYMBOL, "BUY", 2000.0, 0.0, sl=1990.0, tp=2020.0)
        assert pos is None
        assert len(broker.positions) == 0

    def test_commission_deducted(self, broker):
        initial = broker.balance
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2020.0)
        assert broker.balance < initial


# ────────────────────────────────────────────────────────────────────────────
class TestSLTP:
    def test_stop_loss_long(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2100.0)
        closed = broker.update_price(1988.0)   # below SL
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "STOP_LOSS"
        assert len(broker.positions) == 0

    def test_take_profit_long(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2100.0)
        closed = broker.update_price(2105.0)   # above TP
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "TAKE_PROFIT"

    def test_stop_loss_short(self, broker):
        broker.open_position(SYMBOL, "SELL", 2000.0, 1.0, sl=2010.0, tp=1980.0)
        closed = broker.update_price(2012.0)   # above SL
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "STOP_LOSS"

    def test_take_profit_short(self, broker):
        broker.open_position(SYMBOL, "SELL", 2000.0, 1.0, sl=2010.0, tp=1980.0)
        closed = broker.update_price(1978.0)   # below TP
        assert len(closed) == 1
        assert closed[0]["exit_reason"] == "TAKE_PROFIT"

    def test_no_close_mid_range(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2100.0)
        closed = broker.update_price(2050.0)   # well inside range
        assert len(closed) == 0
        assert len(broker.positions) == 1


# ────────────────────────────────────────────────────────────────────────────
class TestPnL:
    def test_winning_long_pnl_positive(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2050.0)
        broker.update_price(2040.0)
        pos = list(broker.positions.values())[0]
        assert pos.unrealized_pnl > 0

    def test_losing_long_pnl_negative(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1950.0, tp=2100.0)
        broker.update_price(1995.0)
        pos = list(broker.positions.values())[0]
        assert pos.unrealized_pnl < 0

    def test_realized_pnl_recorded_after_tp(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2020.0)
        broker.update_price(2021.0)
        assert broker.realized_pnl != 0
        trade = broker.closed_trades[-1]
        assert trade["realized_pnl"] != 0


# ────────────────────────────────────────────────────────────────────────────
class TestEquity:
    def test_equity_tracks_unrealized(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 2.0, sl=1990.0, tp=2100.0)
        broker.update_price(2010.0)
        assert abs(broker.equity - (broker.balance + broker.unrealized_pnl)) < 0.01

    def test_equity_increases_after_win(self, broker):
        start = broker.equity
        broker.open_position(SYMBOL, "BUY", 2000.0, 1.0, sl=1990.0, tp=2020.0)
        broker.update_price(2025.0)   # TP hit
        assert broker.equity > start - 1   # net of commission


# ────────────────────────────────────────────────────────────────────────────
class TestCloseAll:
    def test_close_all_flattens(self, broker):
        broker.open_position(SYMBOL, "BUY", 2000.0, 0.5, sl=1990.0, tp=2100.0)
        broker.open_position(SYMBOL, "SELL", 2000.0, 0.5, sl=2010.0, tp=1900.0)
        closed = broker.close_all(2000.0, "FLATTEN")
        assert len(closed) == 2
        assert len(broker.positions) == 0


# ────────────────────────────────────────────────────────────────────────────
class TestSummary:
    def test_summary_has_all_keys(self, broker):
        s = broker.summary()
        for key in ["balance", "equity", "unrealized_pnl", "realized_pnl",
                    "open_positions", "closed_trades"]:
            assert key in s

    def test_initial_summary(self, broker):
        s = broker.summary()
        assert s["balance"] == 10_000.0
        assert s["open_positions"] == 0
        assert s["closed_trades"] == 0
