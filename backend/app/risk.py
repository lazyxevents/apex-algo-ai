from __future__ import annotations

from math import isfinite


def _number(value: float, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def calculate_underlying_stop_distance(
    direction: str,
    spot: float,
    previous_low: float,
    previous_high: float,
    buffer_points: float,
    atr_points: float,
    minimum_atr_multiple: float,
) -> dict:
    """Keep the invalidation beyond recent structure and outside ordinary ATR noise."""
    spot = max(0.0, _number(spot))
    low = _number(previous_low, spot)
    high = _number(previous_high, spot)
    buffer = max(0.0, _number(buffer_points))
    atr = max(0.0, _number(atr_points))
    minimum_atr = max(0.0, _number(minimum_atr_multiple))

    if str(direction).upper() == "CE":
        structural = max(0.0, spot - low + buffer)
    elif str(direction).upper() == "PE":
        structural = max(0.0, high - spot + buffer)
    else:
        raise ValueError("direction must be CE or PE")

    atr_floor = atr * minimum_atr
    distance = max(structural, atr_floor)
    return {
        "structuralPoints": round(structural, 4),
        "atrFloorPoints": round(atr_floor, 4),
        "atrPoints": round(atr, 4),
        "minimumAtrMultiple": minimum_atr,
        "distancePoints": round(distance, 2),
    }


def calculate_option_premium_stop_distance(
    entry: float,
    delta: float,
    gamma: float,
    theta: float,
    underlying_distance: float,
    expected_hold_minutes: float,
    spread_pct: float,
    spread_multiple: float = 2.0,
) -> dict:
    """Map an underlying invalidation to premium risk using Greeks plus a spread floor."""
    entry = max(0.0, _number(entry))
    if entry <= 0:
        raise ValueError("entry premium must be positive")

    underlying_distance = max(0.0, _number(underlying_distance))
    delta_component = abs(_number(delta)) * underlying_distance
    gamma_component = 0.5 * abs(_number(gamma)) * underlying_distance ** 2
    theta_component = abs(_number(theta)) * max(1.0, _number(expected_hold_minutes, 1.0)) / 375.0
    greek_distance = delta_component + gamma_component + theta_component

    spread_points = entry * max(0.0, _number(spread_pct)) / 100.0
    spread_floor = max(0.05, spread_points * max(1.0, _number(spread_multiple, 2.0)) + max(0.05, entry * 0.001))
    distance = max(greek_distance, spread_floor)

    return {
        "deltaPoints": round(delta_component, 4),
        "gammaPoints": round(gamma_component, 4),
        "thetaPoints": round(theta_component, 4),
        "greekDistancePoints": round(greek_distance, 4),
        "spreadPoints": round(spread_points, 4),
        "spreadFloorPoints": round(spread_floor, 4),
        "distancePoints": round(distance, 2),
    }


def calculate_scalp_target_distance(
    entry: float,
    stop_distance: float,
    net_target_pct: float,
    reward_risk_ratio: float,
    minimum_target_points: float,
    maximum_target_points: float,
) -> float | None:
    """Require the configured minimum target and reward/risk; never silently compress RR."""
    entry = max(0.0, _number(entry))
    stop_distance = max(0.0, _number(stop_distance))
    percent_target = entry * max(0.0, _number(net_target_pct)) / 100.0
    rr_target = stop_distance * max(0.1, _number(reward_risk_ratio, 1.8))
    minimum_target = max(0.05, _number(minimum_target_points, 0.05))
    maximum_target = max(0.05, _number(maximum_target_points, 0.05))
    required = max(percent_target, rr_target, minimum_target)
    if required > maximum_target:
        return None
    return round(required, 2)
