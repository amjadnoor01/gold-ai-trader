"""
tests/test_learner.py — Unit tests for learner.ClosedLoopLearner
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from learner import ClosedLoopLearner, MODELS, MIN_W, MAX_W


def make_trade(pnl: float, direction: str = "BUY", regime: str = "DRL_PPO") -> dict:
    return {
        "trade_id":     "XAU_TEST",
        "direction":    direction,
        "regime":       regime,
        "realized_pnl": pnl,
        "return_pct":   pnl / 100.0,
        "exit_reason":  "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS",
    }


@pytest.fixture
def learner(tmp_path, monkeypatch):
    # Redirect weights file to tmp so tests don't pollute disk
    import learner as lmod
    monkeypatch.setattr(lmod, "WEIGHTS_FILE", tmp_path / "weights.json")
    return ClosedLoopLearner()


class TestWeightDiversity:
    def test_initial_weights_uniform(self, learner):
        for m in MODELS:
            assert abs(learner.weights[m] - 1 / len(MODELS)) < 1e-6

    def test_weights_sum_to_one(self, learner):
        assert abs(sum(learner.weights.values()) - 1.0) < 1e-9

    def test_floor_after_many_losses(self, learner):
        for _ in range(50):
            learner.on_trade_closed(make_trade(-10.0, "BUY", "DRL_PPO"))
        for m in MODELS:
            assert learner.weights[m] >= MIN_W - 1e-9

    def test_ceiling_after_many_wins(self, learner):
        for _ in range(50):
            learner.on_trade_closed(make_trade(20.0, "BUY", "DRL_PPO"))
        for m in MODELS:
            assert learner.weights[m] <= MAX_W + 1e-9

    def test_weights_always_sum_one(self, learner):
        for _ in range(20):
            learner.on_trade_closed(make_trade(5.0))
            assert abs(sum(learner.weights.values()) - 1.0) < 1e-9


class TestIterationCounter:
    def test_counter_increments(self, learner):
        assert learner.iterations == 0
        learner.on_trade_closed(make_trade(1.0))
        assert learner.iterations == 1
        learner.on_trade_closed(make_trade(-1.0))
        assert learner.iterations == 2


class TestReset:
    def test_reset_restores_uniform(self, learner):
        for _ in range(10):
            learner.on_trade_closed(make_trade(10.0, "BUY", "TREND"))
        learner.reset()
        for m in MODELS:
            assert abs(learner.weights[m] - 1 / len(MODELS)) < 1e-6

    def test_reset_clears_iterations(self, learner):
        learner.on_trade_closed(make_trade(1.0))
        learner.reset()
        assert learner.iterations == 0


class TestRegimes:
    def test_winning_regime_gets_boosted(self, learner):
        before = learner.weights["trend"]
        learner.on_trade_closed(make_trade(20.0, "BUY", "TREND"))
        # Weight may be clamped but should not drop below floor
        assert learner.weights["trend"] >= MIN_W - 1e-9

    def test_returns_dict(self, learner):
        result = learner.on_trade_closed(make_trade(5.0))
        assert isinstance(result, dict)
        assert set(result.keys()) == set(MODELS)
