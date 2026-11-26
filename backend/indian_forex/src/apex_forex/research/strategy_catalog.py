"""Small deterministic Forex strategy candidate library.

Signals are research candidates only. They do not place orders and must pass
out-of-sample and cost-aware promotion gates before paper deployment.
"""
from dataclasses import dataclass
from statistics import mean, pstdev

from apex_forex.intelligence.features import ema


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    family: str
    description: str
    minimum_bars: int


STRATEGIES = (
    StrategySpec("ema_trend", "trend", "Fast/slow EMA direction with crossover confirmation", 55),
    StrategySpec("rsi_reversion", "mean_reversion", "RSI extreme with close back inside threshold", 30),
    StrategySpec("donchian_breakout", "breakout", "Close breaks previous rolling high or low", 21),
    StrategySpec("bollinger_reversion", "mean_reversion", "Close returns inside rolling volatility band", 30),
    StrategySpec("range_expansion", "volatility", "Range expansion aligned with directional close", 25),
)


def _closes(candles: list[dict]) -> list[float]:
    values = [float(c["close"]) for c in candles]
    if any(value <= 0 for value in values):
        raise ValueError("Forex candle prices must be positive")
    return values


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    gains = sum(max(change, 0) for change in changes) / period
    losses = sum(max(-change, 0) for change in changes) / period
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def generate_signal(strategy_id: str, candles: list[dict]) -> str | None:
    """Return LONG, SHORT or None using closed candles only."""
    spec = next((s for s in STRATEGIES if s.strategy_id == strategy_id), None)
    if spec is None:
        raise ValueError(f"Unknown strategy: {strategy_id}")
    if len(candles) < spec.minimum_bars:
        return None
    closes = _closes(candles)
    last = closes[-1]
    previous = closes[-2]

    if strategy_id == "ema_trend":
        fast_now, slow_now = ema(closes, 20), ema(closes, 50)
        fast_prev, slow_prev = ema(closes[:-1], 20), ema(closes[:-1], 50)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return None
        if fast_prev <= slow_prev and fast_now > slow_now:
            return "LONG"
        if fast_prev >= slow_prev and fast_now < slow_now:
            return "SHORT"
        return None

    if strategy_id == "rsi_reversion":
        rsi_now, rsi_prev = _rsi(closes), _rsi(closes[:-1])
        if rsi_now is None or rsi_prev is None:
            return None
        if rsi_prev < 30 <= rsi_now:
            return "LONG"
        if rsi_prev > 70 >= rsi_now:
            return "SHORT"
        return None

    if strategy_id == "donchian_breakout":
        prior = candles[-21:-1]
        upper = max(float(c["high"]) for c in prior)
        lower = min(float(c["low"]) for c in prior)
        return "LONG" if last > upper else "SHORT" if last < lower else None

    if strategy_id == "bollinger_reversion":
        window = closes[-20:]
        basis = mean(window)
        deviation = pstdev(window)
        prior_window = closes[-21:-1]
        prior_basis = mean(prior_window)
        prior_dev = pstdev(prior_window)
        if previous < prior_basis - 2 * prior_dev and last >= basis - 2 * deviation:
            return "LONG"
        if previous > prior_basis + 2 * prior_dev and last <= basis + 2 * deviation:
            return "SHORT"
        return None

    if strategy_id == "range_expansion":
        ranges = [float(c["high"]) - float(c["low"]) for c in candles[-21:]]
        baseline = mean(ranges[:-1])
        current = ranges[-1]
        if baseline <= 0 or current < baseline * 1.5:
            return None
        return "LONG" if last > previous else "SHORT" if last < previous else None

    return None


def strategy_catalog() -> list[dict]:
    return [
        {"strategy_id": item.strategy_id, "family": item.family,
         "description": item.description, "minimum_bars": item.minimum_bars,
         "status": "RESEARCH_CANDIDATE_NOT_PROMOTED"}
        for item in STRATEGIES
    ]
