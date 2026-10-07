from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .core import SessionLocal, settings
from .market_research import _atr, _ema, _patterns, _rsi, _structure, _trend
from .models import ResearchActivity, StrategyHypothesis

IST = ZoneInfo(settings.timezone)

LAB_STRATEGIES = {
    "SMC_LIQUIDITY_REVERSAL_SCALP": {
        "family": "SMC_REVERSAL",
        "description": "Liquidity sweep reclaim with directional candle/structure confirmation.",
    },
    "BOS_FVG_CONTINUATION_SCALP": {
        "family": "SMC_CONTINUATION",
        "description": "Break of structure with FVG or directional momentum continuation.",
    },
    "EMA_PULLBACK_MOMENTUM": {
        "family": "MOMENTUM",
        "description": "Directional EMA trend with controlled pullback and RSI confirmation.",
    },
    "FAKE_BREAKOUT_REVERSAL": {
        "family": "PRICE_ACTION",
        "description": "Failed support/resistance break followed by reclaim/rejection.",
    },
    "BREAKOUT_RETEST_MOMENTUM": {
        "family": "BREAKOUT",
        "description": "Strong directional break with structure and momentum evidence.",
    },
    "FIB_STRUCTURE_RETRACE": {
        "family": "FIBONACCI",
        "description": "38.2%-61.8% retracement aligned with directional structure.",
    },
}


def _setup_candidates(prefix: list[dict]) -> list[tuple[str, str]]:
    if len(prefix) < 40:
        return []
    cur = prefix[-1]
    close = float(cur["close"])
    high = float(cur["high"])
    low = float(cur["low"])
    opens = float(cur["open"])
    closes = [float(x["close"]) for x in prefix]
    highs = [float(x["high"]) for x in prefix]
    lows = [float(x["low"]) for x in prefix]
    atr = _atr(prefix)
    if atr <= 0:
        return []

    smc = _structure(prefix)
    pats = _patterns(prefix)
    trend = _trend(prefix)
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi = _rsi(closes)
    out: list[tuple[str, str]] = []

    sweep = smc.get("liquiditySweep")
    if sweep == "BULL" and (pats["bullish"] or close > opens):
        out.append(("SMC_LIQUIDITY_REVERSAL_SCALP", "CE"))
    if sweep == "BEAR" and (pats["bearish"] or close < opens):
        out.append(("SMC_LIQUIDITY_REVERSAL_SCALP", "PE"))

    bos = smc.get("bos")
    fvg = smc.get("fairValueGap")
    if bos == "BULL" and (fvg == "BULL" or (ema9 > ema21 and rsi >= 52)):
        out.append(("BOS_FVG_CONTINUATION_SCALP", "CE"))
    if bos == "BEAR" and (fvg == "BEAR" or (ema9 < ema21 and rsi <= 48)):
        out.append(("BOS_FVG_CONTINUATION_SCALP", "PE"))

    near_ema = abs(close - ema9) <= atr * 0.35
    if trend.get("trend") == "UP" and ema9 > ema21 and near_ema and rsi >= 50:
        out.append(("EMA_PULLBACK_MOMENTUM", "CE"))
    if trend.get("trend") == "DOWN" and ema9 < ema21 and near_ema and rsi <= 50:
        out.append(("EMA_PULLBACK_MOMENTUM", "PE"))

    fake = smc.get("fakeBreakout")
    if fake == "BULL":
        out.append(("FAKE_BREAKOUT_REVERSAL", "CE"))
    elif fake == "BEAR":
        out.append(("FAKE_BREAKOUT_REVERSAL", "PE"))

    strong_bull = "BREAKOUT_CLOSE" in pats["bullish"] or "STRONG_BULL_BODY" in pats["bullish"]
    strong_bear = "BREAKDOWN_CLOSE" in pats["bearish"] or "STRONG_BEAR_BODY" in pats["bearish"]
    if (bos == "BULL" or strong_bull) and ema9 > ema21 and rsi >= 54:
        out.append(("BREAKOUT_RETEST_MOMENTUM", "CE"))
    if (bos == "BEAR" or strong_bear) and ema9 < ema21 and rsi <= 46:
        out.append(("BREAKOUT_RETEST_MOMENTUM", "PE"))

    swing_high = max(highs[-30:])
    swing_low = min(lows[-30:])
    span = max(1e-9, swing_high - swing_low)
    fib382 = swing_high - span * 0.382
    fib618 = swing_high - span * 0.618
    in_zone = min(fib382, fib618) <= close <= max(fib382, fib618)
    if in_zone and trend.get("trend") == "UP" and close >= ema21:
        out.append(("FIB_STRUCTURE_RETRACE", "CE"))
    if in_zone and trend.get("trend") == "DOWN" and close <= ema21:
        out.append(("FIB_STRUCTURE_RETRACE", "PE"))

    # Deduplicate same strategy/direction pair.
    return list(dict.fromkeys(out))


def _evaluate_trade(candles: list[dict], i: int, direction: str, horizon: int = 10, rr: float = 1.5) -> dict:
    prefix = candles[: i + 1]
    atr = _atr(prefix)
    entry = float(candles[i]["close"])
    if atr <= 0:
        return {"outcome": "SKIP", "r": 0.0, "mfeR": 0.0, "maeR": 0.0}
    stop = entry - atr if direction == "CE" else entry + atr
    target = entry + atr * rr if direction == "CE" else entry - atr * rr
    mfe = mae = 0.0
    outcome = "TIMEOUT"
    final_r = 0.0
    future = candles[i + 1:i + 1 + horizon]
    for bar in future:
        hi, lo = float(bar["high"]), float(bar["low"])
        favorable = hi - entry if direction == "CE" else entry - lo
        adverse = entry - lo if direction == "CE" else hi - entry
        mfe = max(mfe, favorable / atr)
        mae = max(mae, adverse / atr)
        target_hit = hi >= target if direction == "CE" else lo <= target
        stop_hit = lo <= stop if direction == "CE" else hi >= stop
        if target_hit and stop_hit:
            outcome, final_r = "AMBIGUOUS_STOP_FIRST", -1.0
            break
        if stop_hit:
            outcome, final_r = "STOP", -1.0
            break
        if target_hit:
            outcome, final_r = "TARGET", rr
            break
    if outcome == "TIMEOUT" and future:
        last = float(future[-1]["close"])
        raw = (last - entry) / atr if direction == "CE" else (entry - last) / atr
        final_r = max(-1.0, min(rr, raw))
    return {
        "outcome": outcome,
        "r": round(final_r, 4),
        "mfeR": round(mfe, 4),
        "maeR": round(mae, 4),
    }


class StrategyLab:
    def run(self, provider) -> dict:
        now = datetime.now(IST)
        results: dict[str, dict] = {}
        total_tests = 0

        for name, key in settings.underlying_keys.items():
            try:
                candles = (
                    provider.intraday_candles_interval(key, 1)
                    if hasattr(provider, "intraday_candles_interval")
                    else provider.intraday_candles(key)
                )
            except Exception as exc:
                results[name] = {"status": "error", "error": str(exc)[:180]}
                continue
            if len(candles) < 120:
                results[name] = {"status": "insufficient_data", "candles": len(candles)}
                continue

            buckets: dict[str, list[dict]] = {k: [] for k in LAB_STRATEGIES}
            # Sample every second bar to keep repeated nightly research bounded.
            for i in range(60, len(candles) - 12, 2):
                for strategy_name, direction in _setup_candidates(candles[: i + 1]):
                    outcome = _evaluate_trade(candles, i, direction)
                    if outcome["outcome"] == "SKIP":
                        continue
                    buckets[strategy_name].append({
                        "time": candles[i].get("timestamp"),
                        "direction": direction,
                        **outcome,
                    })

            instrument_result: dict[str, dict] = {}
            for strategy_name, rows in buckets.items():
                trades = len(rows)
                wins = sum(1 for x in rows if x["outcome"] == "TARGET")
                losses = sum(1 for x in rows if x["outcome"] in {"STOP", "AMBIGUOUS_STOP_FIRST"})
                timeouts = sum(1 for x in rows if x["outcome"] == "TIMEOUT")
                expectancy = sum(float(x["r"]) for x in rows) / max(1, trades)
                avg_mfe = sum(float(x["mfeR"]) for x in rows) / max(1, trades)
                avg_mae = sum(float(x["maeR"]) for x in rows) / max(1, trades)
                win_rate = wins / max(1, wins + losses) * 100 if wins + losses else 0.0
                score = expectancy * min(1.0, trades / 30.0)
                status = "PROMISING" if trades >= 12 and expectancy > 0.12 and win_rate >= 48 else "WATCH" if trades >= 6 else "LOW_SAMPLE"
                metrics = {
                    "trades": trades,
                    "wins": wins,
                    "losses": losses,
                    "timeouts": timeouts,
                    "winRate": round(win_rate, 2),
                    "expectancyR": round(expectancy, 4),
                    "avgMfeR": round(avg_mfe, 4),
                    "avgMaeR": round(avg_mae, 4),
                    "score": round(score, 4),
                    "status": status,
                    "recentExamples": rows[-5:],
                }
                instrument_result[strategy_name] = metrics
                total_tests += trades

                with SessionLocal() as db:
                    row = db.scalar(select(StrategyHypothesis).where(StrategyHypothesis.name == strategy_name))
                    spec = LAB_STRATEGIES[strategy_name]
                    if row is None:
                        row = StrategyHypothesis(
                            name=strategy_name,
                            family=spec["family"],
                            description=spec["description"],
                            evidence_json="[]",
                            rules_json="{}",
                            status=status,
                            score=round(score, 4),
                        )
                        db.add(row)
                    row.family = spec["family"]
                    row.description = spec["description"]
                    row.evidence_json = json.dumps({"instrument": name, "metrics": metrics}, default=str)
                    row.status = status
                    row.score = round(score, 4)
                    row.updated_at = datetime.now(IST)
                    db.commit()
            results[name] = {
                "status": "completed",
                "candles": len(candles),
                "strategies": instrument_result,
            }

        with SessionLocal() as db:
            db.add(ResearchActivity(
                kind="STRATEGY_LAB",
                stage="BACKTEST",
                title=f"Strategy lab tested {total_tests} historical setup outcomes",
                detail_json=json.dumps({"runAt": now.isoformat(), "results": results}, default=str)[:20000],
            ))
            db.commit()

        return {
            "status": "completed",
            "runAt": now.isoformat(),
            "totalSetupTests": total_tests,
            "markets": results,
        }


strategy_lab = StrategyLab()
