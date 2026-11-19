from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.risk import (
    calculate_execution_learning_penalties,
    calculate_option_premium_stop_distance,
    calculate_scalp_target_distance,
    calculate_underlying_stop_distance,
)


def test_underlying_stop_has_atr_floor_when_recent_candle_is_too_tight():
    plan = calculate_underlying_stop_distance(
        "CE", spot=25000, previous_low=24999, previous_high=25010,
        buffer_points=0.5, atr_points=12, minimum_atr_multiple=0.55,
    )
    assert plan["structuralPoints"] == 1.5
    assert plan["atrFloorPoints"] == 6.6
    assert plan["distancePoints"] == 6.6


def test_option_stop_uses_greeks_and_spread_floor():
    plan = calculate_option_premium_stop_distance(
        entry=100, delta=0.45, gamma=0.001, theta=2,
        underlying_distance=10, expected_hold_minutes=6,
        spread_pct=0.5, spread_multiple=2,
    )
    assert plan["greekDistancePoints"] > 0
    assert plan["spreadFloorPoints"] == 1.05
    assert plan["distancePoints"] == max(
        round(plan["greekDistancePoints"], 2),
        round(plan["spreadFloorPoints"], 2),
    )


def test_target_requires_reward_risk_and_rejects_unachievable_target():
    assert calculate_scalp_target_distance(
        entry=100, stop_distance=4, net_target_pct=2.5,
        reward_risk_ratio=1.8, minimum_target_points=10,
        maximum_target_points=15,
    ) == 10
    assert calculate_scalp_target_distance(
        entry=100, stop_distance=9, net_target_pct=2.5,
        reward_risk_ratio=1.8, minimum_target_points=10,
        maximum_target_points=15,
    ) is None



def test_execution_learning_penalizes_fast_stop_slippage_and_cost_erosion():
    feedback = calculate_execution_learning_penalties(
        {
            "entryPremium": 100,
            "spreadPct": 0.5,
            "holdingSeconds": 2,
            "exitSlippagePoints": 1.0,
            "grossPnl": 20,
        },
        "STOPPED",
        net_pnl=-5,
    )
    assert feedback["fastStopPenalty"] < 0
    assert feedback["slippagePenalty"] < 0
    assert feedback["costErosionPenalty"] < 0
    assert feedback["total"] == -0.45


def test_execution_learning_does_not_penalize_normal_target_trade():
    feedback = calculate_execution_learning_penalties(
        {"entryPremium": 100, "holdingSeconds": 30, "grossPnl": 100},
        "TARGET",
        net_pnl=70,
    )
    assert feedback["total"] == 0
