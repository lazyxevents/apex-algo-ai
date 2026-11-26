"""Evidence-based strategy promotion gates; win rate alone is never sufficient."""
from dataclasses import asdict, dataclass
from math import sqrt
from statistics import mean, pstdev


@dataclass(frozen=True)
class StrategyMetrics:
    trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy_per_trade: float
    max_drawdown_pct: float
    sharpe_like: float
    out_of_sample_trades: int
    out_of_sample_expectancy: float
    eligible: bool
    rejection_reasons: tuple[str, ...]
    score: float


def _max_drawdown_pct(pnls: list[float], initial_equity: float) -> float:
    equity = initial_equity
    peak = initial_equity
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100)
    return worst


def evaluate_strategy(
    in_sample_pnls: list[float],
    out_of_sample_pnls: list[float],
    initial_equity: float,
    *,
    min_trades: int = 100,
    min_oos_trades: int = 30,
    min_profit_factor: float = 1.2,
    max_drawdown_pct: float = 12.0,
) -> StrategyMetrics:
    if initial_equity <= 0:
        raise ValueError("initial_equity must be positive")
    if any(not isinstance(x, (int, float)) for x in in_sample_pnls + out_of_sample_pnls):
        raise ValueError("PnL values must be numeric")
    wins = [float(x) for x in in_sample_pnls if x > 0]
    losses = [abs(float(x)) for x in in_sample_pnls if x < 0]
    total = len(in_sample_pnls)
    gross_win, gross_loss = sum(wins), sum(losses)
    pf = gross_win / gross_loss if gross_loss else (10.0 if gross_win else 0.0)
    expectancy = mean(in_sample_pnls) if total else 0.0
    returns = [float(x) / initial_equity for x in in_sample_pnls]
    deviation = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe_like = (mean(returns) / deviation * sqrt(252)) if deviation else 0.0
    drawdown = _max_drawdown_pct([float(x) for x in in_sample_pnls], initial_equity)
    oos_expectancy = mean(out_of_sample_pnls) if out_of_sample_pnls else 0.0

    reasons = []
    if total < min_trades:
        reasons.append(f"Need at least {min_trades} in-sample trades")
    if len(out_of_sample_pnls) < min_oos_trades:
        reasons.append(f"Need at least {min_oos_trades} out-of-sample trades")
    if expectancy <= 0:
        reasons.append("In-sample net expectancy is not positive")
    if oos_expectancy <= 0:
        reasons.append("out-of-sample net expectancy is not positive")
    if pf < min_profit_factor:
        reasons.append(f"Profit factor below {min_profit_factor}")
    if drawdown > max_drawdown_pct:
        reasons.append(f"Maximum drawdown exceeds {max_drawdown_pct}%")

    # Conservative score: expectancy, PF and drawdown matter; win rate alone cannot promote.
    score = (
        max(-1.0, min(1.0, expectancy / initial_equity * 1000))
        + max(0.0, min(2.0, pf)) * 0.5
        + max(-1.0, min(1.0, oos_expectancy / initial_equity * 1000))
        - drawdown / 100
    )
    return StrategyMetrics(
        trades=total,
        win_rate_pct=(len(wins) / total * 100) if total else 0.0,
        profit_factor=pf,
        expectancy_per_trade=expectancy,
        max_drawdown_pct=drawdown,
        sharpe_like=sharpe_like,
        out_of_sample_trades=len(out_of_sample_pnls),
        out_of_sample_expectancy=oos_expectancy,
        eligible=not reasons,
        rejection_reasons=tuple(reasons),
        score=score,
    )


def metrics_dict(metrics: StrategyMetrics) -> dict:
    result = asdict(metrics)
    result["rejection_reasons"] = list(metrics.rejection_reasons)
    return result
