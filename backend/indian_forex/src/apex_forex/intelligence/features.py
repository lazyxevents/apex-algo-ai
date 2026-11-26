"""Small deterministic feature set; research features are not trade recommendations."""
from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def true_range(high: float, low: float, previous_close: float | None) -> float:
    if high < low:
        raise ValueError("high cannot be below low")
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("OHLC arrays must have equal lengths")
    if len(highs) < period + 1:
        return None
    ranges = [true_range(highs[i], lows[i], closes[i - 1]) for i in range(1, len(highs))]
    return sum(ranges[-period:]) / period
