"""Deterministic candle backtester with conservative stop/target ordering."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BacktestTrade:
    side: Literal["LONG", "SHORT"]
    entry: float
    exit: float
    quantity: float
    gross_pnl: float
    fees: float
    net_pnl: float
    exit_reason: str


def run_bracket_backtest(
    candles: list[dict],
    side: Literal["LONG", "SHORT"],
    quantity: float,
    stop_loss: float,
    target: float,
    fee_per_side: float = 0.0,
    slippage: float = 0.0,
) -> list[BacktestTrade]:
    """Enter at the first candle open; if SL and target both touch, assume SL first."""
    if not candles or quantity <= 0 or fee_per_side < 0 or slippage < 0:
        raise ValueError("Candles, positive quantity and non-negative costs are required")
    first = candles[0]
    entry = float(first["open"]) + slippage if side == "LONG" else float(first["open"]) - slippage
    if side == "LONG" and not stop_loss < entry < target:
        raise ValueError("LONG requires stop_loss < entry < target")
    if side == "SHORT" and not target < entry < stop_loss:
        raise ValueError("SHORT requires target < entry < stop_loss")
    for candle in candles[1:]:
        high, low = float(candle["high"]), float(candle["low"])
        hit_sl = low <= stop_loss if side == "LONG" else high >= stop_loss
        hit_target = high >= target if side == "LONG" else low <= target
        if hit_sl or hit_target:
            reason = "STOP_LOSS" if hit_sl else "TARGET"
            raw_exit = stop_loss if hit_sl else target
            exit_price = raw_exit - slippage if side == "LONG" else raw_exit + slippage
            direction = 1 if side == "LONG" else -1
            gross = (exit_price - entry) * direction * quantity
            fees = fee_per_side * 2
            return [BacktestTrade(
                side, entry, exit_price, quantity, round(gross, 8), fees,
                round(gross - fees, 8), reason,
            )]
    return []
