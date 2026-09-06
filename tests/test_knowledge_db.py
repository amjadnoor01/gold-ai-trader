"""
tests/test_knowledge_db.py — Unit tests for Knowledge Database & POC Audit Engine
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from knowledge_db import KnowledgeDatabase


@pytest.fixture
def db(tmp_path):
    db_file = tmp_path / "test_knowledge.db"
    return KnowledgeDatabase(db_path=db_file)


class TestTradeLogging:
    def test_log_open_and_close(self, db):
        trade = {
            "trade_id": "XAU_TEST_001",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry_price": 2000.0,
            "size": 0.5,
            "sl": 1990.0,
            "tp": 2020.0,
            "confidence": 0.8,
            "regime": "DRL_PPO"
        }
        db.log_trade_opened(trade, {"rsi": 45, "atr": 4.5})

        summary = db.get_knowledge_summary()
        assert summary["total_trades_logged"] == 1
        assert summary["closed_trades"] == 0

        # Close trade
        closed_trade = {
            **trade,
            "exit_price": 2015.0,
            "realized_pnl": 7.5,
            "exit_reason": "TAKE_PROFIT"
        }
        lessons = db.log_trade_closed(closed_trade, {"atr": 4.5, "rsi": 45})

        assert "profitable" in lessons
        summary = db.get_knowledge_summary()
        assert summary["closed_trades"] == 1
        assert summary["winning_trades"] == 1
        assert summary["total_knowledge_pnl"] == 7.5


class TestPatternIntelligence:
    def test_query_setup_intelligence(self, db):
        # Insert historical winning trades
        for i in range(5):
            trade = {
                "trade_id": f"XAU_WIN_{i}",
                "symbol": "XAUUSD",
                "direction": "BUY",
                "entry_price": 2000.0 + i,
                "exit_price": 2010.0 + i,
                "size": 0.5,
                "sl": 1990.0,
                "tp": 2020.0,
                "realized_pnl": 10.0,
            }
            db.log_trade_opened(trade)
            db.log_trade_closed(trade, {"atr": 5.0, "rsi": 55.0})

        modifier, rationale = db.query_setup_intelligence({"direction": "BUY", "atr": 5.1, "rsi": 54.0})
        assert modifier >= 1.0
        assert "HIGH CONFIDENCE" in rationale or "win rate" in rationale.lower()


class TestPocRecordKeeping:
    def test_record_poc_milestone(self, db):
        account = {"closed_trades": 10, "equity": 10250.0, "realized_pnl": 250.0}
        metrics = {"win_rate": 0.7, "sharpe": 1.5, "max_drawdown": 0.02, "profit_factor": 2.1}
        weights = {"ppo": 0.4, "trend": 0.3}

        db.record_poc_milestone(account, metrics, weights, "Test Milestone")

        history = db.get_poc_history()
        assert len(history) == 1
        assert history[0]["equity"] == 10250.0
        assert history[0]["win_rate"] == 0.7
