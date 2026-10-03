import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from math import isfinite

from sqlalchemy import select

from .core import SessionLocal, settings
from .models import StrategyState, Trade


@dataclass(frozen=True)
class StrategyArm:
    name: str
    ema_fast: int
    ema_slow: int
    rsi_bull: float
    rsi_bear: float
    breakout_lookback: int
    volume_ratio: float
    min_score: float


ARMS = [
    StrategyArm("APEX_BREAKOUT_BALANCED", 9, 21, 56, 44, 15, 1.05, settings.signal_min_score),
    StrategyArm("APEX_BREAKOUT_FAST", 8, 18, 58, 42, 12, 1.10, min(0.82, settings.signal_min_score + 0.03)),
    StrategyArm("APEX_BREAKOUT_STABLE", 12, 26, 55, 45, 20, 1.00, max(0.55, settings.signal_min_score - 0.02)),
]


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        return values[-1]
    alpha = 2 / (period + 1)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = alpha * value + (1 - alpha) * out
    return out


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = changes[-period:]
    gains = sum(max(x, 0) for x in recent) / period
    losses = sum(max(-x, 0) for x in recent) / period
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def _atr(candles: list[dict], period: int = 14) -> float:
    if len(candles) <= period:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        high, low = candles[i]["high"], candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def evaluate_signal(candles: list[dict], arm: StrategyArm) -> dict:
    minimum = max(arm.ema_slow + 5, arm.breakout_lookback + 3, 30)
    if len(candles) < minimum:
        return {"action": "NO_TRADE", "score": 0.0, "reason": f"need {minimum} candles"}

    candles = sorted(candles, key=lambda x: x["timestamp"])
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c.get("volume", 0) or 0) for c in candles]

    close = closes[-1]
    fast = _ema(closes, arm.ema_fast)
    slow = _ema(closes, arm.ema_slow)
    rsi = _rsi(closes)
    atr = _atr(candles)
    atr_pct = (atr / close * 100) if close else 0
    ref_high = max(highs[-arm.breakout_lookback - 1:-1])
    ref_low = min(lows[-arm.breakout_lookback - 1:-1])
    volume_base = sum(volumes[-11:-1]) / max(1, len(volumes[-11:-1]))
    volume_ratio = volumes[-1] / volume_base if volume_base > 0 else 1.0
    green = closes[-1] > float(candles[-1]["open"])
    red = closes[-1] < float(candles[-1]["open"])
    volatility_ok = 0.04 <= atr_pct <= 2.5

    bull = 0.0
    bull += 0.30 if fast > slow else 0
    bull += 0.20 if rsi >= arm.rsi_bull else 0
    bull += 0.25 if close > ref_high else 0
    bull += 0.10 if volume_ratio >= arm.volume_ratio else 0
    bull += 0.10 if green else 0
    bull += 0.05 if volatility_ok else 0

    bear = 0.0
    bear += 0.30 if fast < slow else 0
    bear += 0.20 if rsi <= arm.rsi_bear else 0
    bear += 0.25 if close < ref_low else 0
    bear += 0.10 if volume_ratio >= arm.volume_ratio else 0
    bear += 0.10 if red else 0
    bear += 0.05 if volatility_ok else 0

    if max(bull, bear) < arm.min_score:
        action = "NO_TRADE"
        score = max(bull, bear)
    elif bull > bear:
        action, score = "CE", bull
    else:
        action, score = "PE", bear

    return {
        "action": action,
        "score": round(score, 4),
        "underlyingPrice": close,
        "emaFast": round(fast, 2),
        "emaSlow": round(slow, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "atrPct": round(atr_pct, 4),
        "volumeRatio": round(volume_ratio, 2),
        "breakoutHigh": ref_high,
        "breakoutLow": ref_low,
        "strategy": arm.name,
    }


def select_option(chain: list[dict], direction: str, spot: float, deployable_capital: float) -> dict | None:
    candidates: list[dict] = []
    strikes = sorted({float(row.get("strike_price") or 0) for row in chain if float(row.get("strike_price") or 0) > 0})
    if not strikes:
        return None
    atm_index = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    if direction == "CE":
        allowed_strikes = set(strikes[atm_index: atm_index + settings.max_otm_steps + 1])
    else:
        start = max(0, atm_index - settings.max_otm_steps)
        allowed_strikes = set(strikes[start: atm_index + 1])

    for row in chain:
        strike = float(row.get("strike_price") or 0)
        if strike not in allowed_strikes:
            continue
        side = row.get("call_options") if direction == "CE" else row.get("put_options")
        if not side:
            continue
        market = side.get("market_data") or {}
        greeks = side.get("option_greeks") or {}
        ltp = float(market.get("ltp") or 0)
        bid = float(market.get("bid_price") or 0)
        ask = float(market.get("ask_price") or 0)
        volume = int(market.get("volume") or 0)
        delta = abs(float(greeks.get("delta") or 0))
        instrument_key = side.get("instrument_key")
        if not instrument_key or ltp <= 0:
            continue
        spread_pct = ((ask - bid) / ltp * 100) if ask > 0 and bid > 0 and ask >= bid else 999.0
        if spread_pct > settings.max_option_spread_pct or volume < settings.min_option_volume:
            continue

        distance_pct = abs(strike - spot) / max(spot, 1) * 100
        affordable = deployable_capital / ltp
        delta_score = max(0.0, 1 - abs(delta - settings.target_delta) / max(settings.target_delta, 0.01))
        spread_score = max(0.0, 1 - spread_pct / max(settings.max_option_spread_pct, 0.01))
        liquidity_score = min(1.0, volume / max(settings.min_option_volume * 5, 1))
        distance_score = max(0.0, 1 - distance_pct / 2.0)
        affordability_score = min(1.0, affordable / 100.0)
        score = 0.35 * delta_score + 0.25 * spread_score + 0.20 * liquidity_score + 0.10 * distance_score + 0.10 * affordability_score
        candidates.append({
            "instrumentKey": instrument_key,
            "strike": strike,
            "ltp": ltp,
            "bid": bid,
            "ask": ask,
            "volume": volume,
            "oi": int(market.get("oi") or 0),
            "delta": delta,
            "gamma": float(greeks.get("gamma") or 0),
            "theta": float(greeks.get("theta") or 0),
            "vega": float(greeks.get("vega") or 0),
            "iv": float(greeks.get("iv") or 0),
            "spreadPct": round(spread_pct, 3),
            "selectionScore": round(score, 4),
        })

    if not candidates:
        return None
    candidates.sort(key=lambda x: x["selectionScore"], reverse=True)
    return candidates[0]


class AdaptiveLearner:
    def _get_state(self, db) -> StrategyState:
        state = db.scalar(select(StrategyState).where(StrategyState.name == "APEX_ADAPTIVE_V1"))
        if not state:
            state = StrategyState(name="APEX_ADAPTIVE_V1")
            db.add(state)
            db.commit()
            db.refresh(state)
        return state

    def snapshot(self) -> dict:
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            for arm in ARMS:
                q_values.setdefault(arm.name, 0.0)
                counts.setdefault(arm.name, 0)
            best = max(ARMS, key=lambda a: q_values.get(a.name, 0.0)).name
            return {
                "enabled": settings.adaptive_learning_enabled,
                "method": "bounded epsilon-greedy multi-armed bandit",
                "qValues": q_values,
                "counts": counts,
                "bestArm": best,
                "explorationRate": settings.exploration_rate,
                "learningRate": settings.learning_rate,
                "arms": [asdict(a) for a in ARMS],
            }

    def choose_arm(self) -> StrategyArm:
        snap = self.snapshot()
        q_values = snap["qValues"]
        total = sum(snap["counts"].values())
        if not settings.adaptive_learning_enabled or total < settings.minimum_learning_trades:
            return ARMS[0]
        if random.random() < settings.exploration_rate:
            return random.choice(ARMS)
        return max(ARMS, key=lambda a: q_values.get(a.name, 0.0))

    def learn(self) -> dict:
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            rows = db.execute(
                select(Trade).where(Trade.status != "OPEN", Trade.id > state.last_processed_trade_id).order_by(Trade.id.asc())
            ).scalars().all()
            processed = 0
            for trade in rows:
                state.last_processed_trade_id = max(state.last_processed_trade_id, trade.id)
                try:
                    meta = json.loads(trade.reason or "{}")
                except json.JSONDecodeError:
                    continue
                arm = meta.get("strategy")
                initial_risk = float(meta.get("initialRisk") or 0)
                if arm not in {a.name for a in ARMS} or initial_risk <= 0:
                    continue
                reward = trade.pnl / initial_risk
                if not isfinite(reward):
                    continue
                reward = max(-2.0, min(2.0, reward))
                old = float(q_values.get(arm, 0.0))
                q_values[arm] = round(old + settings.learning_rate * (reward - old), 6)
                counts[arm] = int(counts.get(arm, 0)) + 1
                processed += 1
            if rows:
                state.q_values_json = json.dumps(q_values)
                state.counts_json = json.dumps(counts)
                state.updated_at = datetime.now(timezone.utc)
                db.commit()
            return {"processed": processed, "qValues": q_values, "counts": counts}


adaptive_learner = AdaptiveLearner()
