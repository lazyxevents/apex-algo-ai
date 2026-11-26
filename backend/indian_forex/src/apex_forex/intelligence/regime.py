"""Transparent baseline regime classifier; replace only after out-of-sample validation."""
from enum import StrEnum
from .features import ema, atr


class MarketRegime(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


def classify_regime(closes: list[float], highs: list[float], lows: list[float],
                    fast_period: int = 20, slow_period: int = 50,
                    atr_period: int = 14, volatility_threshold: float = 0.02) -> MarketRegime:
    if len(closes) != len(highs) or len(closes) != len(lows):
        raise ValueError("OHLC arrays must have equal lengths")
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    current_atr = atr(highs, lows, closes, atr_period)
    if fast is None or slow is None or current_atr is None or closes[-1] <= 0:
        return MarketRegime.INSUFFICIENT_DATA
    if current_atr / closes[-1] >= volatility_threshold:
        return MarketRegime.HIGH_VOLATILITY
    if fast > slow and closes[-1] > fast:
        return MarketRegime.BULL_TREND
    if fast < slow and closes[-1] < fast:
        return MarketRegime.BEAR_TREND
    return MarketRegime.RANGE
