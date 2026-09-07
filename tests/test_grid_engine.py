"""
tests/test_grid_engine.py — Unit tests for Intelligent Grid Trading Engine
"""
import pytest
from grid_engine import IntelligentGridEngine
from paper_broker import PaperBroker


def test_grid_regime_determination():
    engine = IntelligentGridEngine()

    # Ranging / Neutral
    assert engine._determine_grid_regime(0.05, "MEAN_REV") == "BILATERAL_NEUTRAL"
    assert engine._determine_grid_regime(-0.08, "NEUTRAL") == "BILATERAL_NEUTRAL"

    # Bullish Trend
    assert engine._determine_grid_regime(0.35, "TREND") == "TREND_BIASED_BULL"
    assert engine._determine_grid_regime(0.25, "MOMENTUM") == "TREND_BIASED_BULL"

    # Bearish Trend
    assert engine._determine_grid_regime(-0.35, "TREND") == "TREND_BIASED_BEAR"
    assert engine._determine_grid_regime(-0.25, "MOMENTUM") == "TREND_BIASED_BEAR"


def test_grid_deployment_and_spacing():
    engine = IntelligentGridEngine()
    broker = PaperBroker(initial_balance=10000.0)
    directive = {
        "grid_enabled": True,
        "grid_mode": "AUTO",
        "grid_levels": 6,
        "grid_spacing_atr_mult": 0.30,
        "grid_max_risk_pct": 0.015
    }
    ens_res = {"regime_lead": "MEAN_REV", "consensus_score": 0.05}

    # Spot price = 4420.0, ATR = 20.0 -> spacing = 6.0
    engine.update(spot_price=4420.0, atr=20.0, ensemble_res=ens_res,
                  kb_mod=1.0, broker=broker, directive=directive)

    state = engine.get_grid_state()
    assert state["enabled"] is True
    assert state["active_regime"] == "BILATERAL_NEUTRAL"
    assert state["center_price"] == 4420.0
    assert state["spacing"] == 6.0
    assert state["total_rungs"] == 6
    assert state["pending_rungs_count"] == 6

    # 3 buy rungs below 4420.0: 4414, 4408, 4402
    # 3 sell rungs above 4420.0: 4426, 4432, 4438
    buy_rungs = [r for r in state["rungs"] if r["type"] == "BUY"]
    sell_rungs = [r for r in state["rungs"] if r["type"] == "SELL"]
    assert len(buy_rungs) == 3
    assert len(sell_rungs) == 3
    assert buy_rungs[-1]["price"] == 4414.0
    assert sell_rungs[0]["price"] == 4426.0


def test_grid_trigger_and_harvest():
    engine = IntelligentGridEngine()
    broker = PaperBroker(initial_balance=10000.0)
    directive = {
        "grid_enabled": True,
        "grid_mode": "AUTO",
        "grid_levels": 6,
        "grid_spacing_atr_mult": 0.30,
        "grid_max_risk_pct": 0.015
    }
    ens_res = {"regime_lead": "MEAN_REV", "consensus_score": 0.0}

    engine.update(spot_price=4420.0, atr=20.0, ensemble_res=ens_res,
                  kb_mod=1.0, broker=broker, directive=directive)

    # Move price down to touch buy rung at 4414.0
    engine.update(spot_price=4413.5, atr=20.0, ensemble_res=ens_res,
                  kb_mod=1.0, broker=broker, directive=directive)

    state = engine.get_grid_state()
    assert state["filled_rungs_count"] >= 1
    assert len(broker.positions) >= 1

    # Find the opened position
    pos = list(broker.positions.values())[0]
    assert pos.direction == "BUY"

    # Price moves up to hit TP
    broker.update_price(pos.tp + 1.0)
    assert len(broker.positions) == 0

    # Next grid update syncs closed trade
    engine.update(spot_price=pos.tp + 1.0, atr=20.0, ensemble_res=ens_res,
                  kb_mod=1.0, broker=broker, directive=directive)

    state2 = engine.get_grid_state()
    assert state2["total_harvested_pnl"] > 0
    assert state2["total_grid_trades"] == 1
    assert state2["grid_win_rate"] == 100.0


if __name__ == "__main__":
    test_grid_regime_determination()
    test_grid_deployment_and_spacing()
    test_grid_trigger_and_harvest()
    print("All Intelligent Grid Unit Tests Passed Successfully!")
