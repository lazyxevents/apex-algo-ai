"""Forex position sizing with explicit account-currency conversion and cost buffer."""
from dataclasses import asdict, dataclass
from math import floor


@dataclass(frozen=True)
class PositionSize:
    lots: float
    units: float
    estimated_risk_account_ccy: float
    stop_pips: float
    cost_buffer_pips: float
    accepted: bool
    reason: str


def calculate_position_size(
    *,
    risk_budget_account_ccy: float,
    entry_price: float,
    stop_price: float,
    pip_size: float,
    quote_to_account_rate: float,
    round_trip_cost_pips: float = 0.0,
    contract_units_per_lot: float = 100_000,
    lot_step: float = 0.01,
    min_lot: float = 0.01,
    max_lot: float = 100.0,
) -> PositionSize:
    values = (risk_budget_account_ccy, entry_price, stop_price, pip_size,
              quote_to_account_rate, contract_units_per_lot, lot_step, min_lot, max_lot)
    if any(value <= 0 for value in values):
        raise ValueError("Risk, prices, pip size, conversion and lot constraints must be positive")
    if round_trip_cost_pips < 0:
        raise ValueError("round_trip_cost_pips cannot be negative")
    distance = abs(entry_price - stop_price)
    stop_pips = distance / pip_size
    total_risk_distance = distance + round_trip_cost_pips * pip_size
    risk_per_unit = total_risk_distance * quote_to_account_rate
    raw_units = risk_budget_account_ccy / risk_per_unit
    raw_lots = raw_units / contract_units_per_lot
    lots = floor(raw_lots / lot_step) * lot_step
    if lots < min_lot:
        return PositionSize(0, 0, 0, stop_pips, round_trip_cost_pips, False,
                            "Risk budget is too small for broker minimum lot")
    lots = min(lots, max_lot)
    units = lots * contract_units_per_lot
    estimated = units * risk_per_unit
    return PositionSize(round(lots, 4), round(units, 2), round(estimated, 2),
                        round(stop_pips, 2), round(round_trip_cost_pips, 2), True,
                        "Position size calculated; apply broker margin and exposure checks")


def position_size_dict(result: PositionSize) -> dict:
    return asdict(result)
