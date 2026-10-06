import json
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .core import SessionLocal, settings
from .models import ResearchRun, StrategyState, Trade

IST = ZoneInfo(settings.timezone)


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
    style: str = "breakout"


ARMS = [
    StrategyArm("APEX_BREAKOUT_BALANCED", 9, 21, 56, 44, 15, 1.05, settings.signal_min_score),
    StrategyArm("APEX_BREAKOUT_FAST", 8, 18, 58, 42, 12, 1.10, min(0.82, settings.signal_min_score + 0.03)),
    StrategyArm("APEX_BREAKOUT_STABLE", 12, 26, 55, 45, 20, 1.00, max(0.55, settings.signal_min_score - 0.02)),
    StrategyArm("APEX_SCALP_MOMENTUM", 5, 13, 60, 40, 8, 1.20, max(0.72, settings.signal_min_score + 0.05), "scalp"),
]


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
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
        high, low = float(candles[i]["high"]), float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def detect_candlestick_patterns(candles: list[dict]) -> dict:
    if len(candles) < 2:
        return {"bullishScore": 0.0, "bearishScore": 0.0, "bullish": [], "bearish": []}
    prev, cur = candles[-2], candles[-1]
    o, h, l, c = map(float, (cur["open"], cur["high"], cur["low"], cur["close"]))
    po, ph, pl, pc = map(float, (prev["open"], prev["high"], prev["low"], prev["close"]))
    body = max(abs(c - o), 1e-9)
    rng = max(h - l, 1e-9)
    upper = h - max(o, c)
    lower = min(o, c) - l
    bullish: list[str] = []
    bearish: list[str] = []

    if lower >= body * 2.0 and upper <= body * 0.8 and c >= o:
        bullish.append("BULLISH_PIN_BAR")
    if upper >= body * 2.0 and lower <= body * 0.8 and c <= o:
        bearish.append("BEARISH_PIN_BAR")
    if c > o and pc < po and o <= pc and c >= po:
        bullish.append("BULLISH_ENGULFING")
    if c < o and pc > po and o >= pc and c <= po:
        bearish.append("BEARISH_ENGULFING")
    if c > ph and c > o:
        bullish.append("BREAKOUT_CLOSE")
    if c < pl and c < o:
        bearish.append("BREAKDOWN_CLOSE")
    if c > o and body / rng >= 0.65:
        bullish.append("STRONG_GREEN_BODY")
    if c < o and body / rng >= 0.65:
        bearish.append("STRONG_RED_BODY")

    return {
        "bullishScore": min(1.0, len(bullish) * 0.5),
        "bearishScore": min(1.0, len(bearish) * 0.5),
        "bullish": bullish,
        "bearish": bearish,
    }


def market_context(candles: list[dict]) -> dict:
    if len(candles) < 22:
        return {"key": "UNKNOWN", "trend": "UNKNOWN", "volatility": "UNKNOWN", "timeBucket": "UNKNOWN"}
    closes = [float(c["close"]) for c in candles]
    fast, slow = _ema(closes, 9), _ema(closes, 21)
    atr = _atr(candles)
    close = max(closes[-1], 1e-9)
    trend_gap = (fast - slow) / close * 100
    if trend_gap > 0.08:
        trend = "UP"
    elif trend_gap < -0.08:
        trend = "DOWN"
    else:
        trend = "FLAT"
    atr_pct = atr / close * 100
    volatility = "HIGH" if atr_pct >= 0.35 else "LOW" if atr_pct <= 0.10 else "NORMAL"
    try:
        ts = datetime.fromisoformat(str(candles[-1]["timestamp"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        hm = ts.astimezone(IST).strftime("%H:%M")
        time_bucket = "OPEN" if hm < "10:30" else "MID" if hm < "14:00" else "LATE"
    except Exception:
        time_bucket = "UNKNOWN"
    patterns = detect_candlestick_patterns(candles)
    bias = "BULL" if patterns["bullishScore"] > patterns["bearishScore"] else "BEAR" if patterns["bearishScore"] > patterns["bullishScore"] else "NONE"
    return {
        "key": f"{trend}|{volatility}|{time_bucket}|{bias}",
        "trend": trend,
        "volatility": volatility,
        "timeBucket": time_bucket,
        "patternBias": bias,
        "atrPct": round(atr_pct, 4),
    }


def eligible_arms(capital: float, context: dict) -> list[StrategyArm]:
    arms = []
    for arm in ARMS:
        if arm.style == "scalp":
            if not settings.allow_scalp_strategy or capital < settings.min_capital_for_scalp:
                continue
            if context.get("volatility") == "LOW" or context.get("trend") == "FLAT":
                continue
        arms.append(arm)
    return arms or [ARMS[0]]


def evaluate_signal(candles: list[dict], arm: StrategyArm) -> dict:
    minimum = max(arm.ema_slow + 5, arm.breakout_lookback + 3, 30)
    if len(candles) < minimum:
        return {"action": "NO_TRADE", "score": 0.0, "reason": f"need {minimum} candles", "strategy": arm.name}

    candles = sorted(candles, key=lambda x: x["timestamp"])
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c.get("volume", 0) or 0) for c in candles]
    close = closes[-1]
    fast, slow = _ema(closes, arm.ema_fast), _ema(closes, arm.ema_slow)
    rsi = _rsi(closes)
    atr = _atr(candles)
    atr_pct = (atr / close * 100) if close else 0
    ref_high = max(highs[-arm.breakout_lookback - 1:-1])
    ref_low = min(lows[-arm.breakout_lookback - 1:-1])
    volume_base = sum(volumes[-11:-1]) / max(1, len(volumes[-11:-1]))
    volume_ratio = volumes[-1] / volume_base if volume_base > 0 else 1.0
    patterns = detect_candlestick_patterns(candles)
    volatility_ok = 0.04 <= atr_pct <= 2.5

    bull = 0.0
    bull += 0.25 if fast > slow else 0
    bull += 0.15 if rsi >= arm.rsi_bull else 0
    bull += 0.20 if close > ref_high else 0
    bull += 0.10 if volume_ratio >= arm.volume_ratio else 0
    bull += 0.20 * patterns["bullishScore"]
    bull += 0.10 if volatility_ok else 0

    bear = 0.0
    bear += 0.25 if fast < slow else 0
    bear += 0.15 if rsi <= arm.rsi_bear else 0
    bear += 0.20 if close < ref_low else 0
    bear += 0.10 if volume_ratio >= arm.volume_ratio else 0
    bear += 0.20 * patterns["bearishScore"]
    bear += 0.10 if volatility_ok else 0

    action, score = "NO_TRADE", max(bull, bear)
    if score >= arm.min_score:
        if bull > bear:
            if not settings.candle_confirmation_required or patterns["bullishScore"] > 0:
                action, score = "CE", bull
        elif bear > bull:
            if not settings.candle_confirmation_required or patterns["bearishScore"] > 0:
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
        "previousHigh": highs[-2],
        "previousLow": lows[-2],
        "previousClose": closes[-2],
        "strategy": arm.name,
        "patterns": patterns,
        "context": market_context(candles),
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
        market, greeks = side.get("market_data") or {}, side.get("option_greeks") or {}
        ltp = float(market.get("ltp") or 0)
        bid, ask = float(market.get("bid_price") or 0), float(market.get("ask_price") or 0)
        volume, delta = int(market.get("volume") or 0), abs(float(greeks.get("delta") or 0))
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
            "instrumentKey": instrument_key, "strike": strike, "ltp": ltp, "bid": bid, "ask": ask,
            "volume": volume, "oi": int(market.get("oi") or 0), "delta": delta,
            "gamma": float(greeks.get("gamma") or 0), "theta": float(greeks.get("theta") or 0),
            "vega": float(greeks.get("vega") or 0), "iv": float(greeks.get("iv") or 0),
            "spreadPct": round(spread_pct, 3), "selectionScore": round(score, 4),
        })
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["selectionScore"])


def _simulate_arm(candles: list[dict], arm: StrategyArm) -> dict:
    if len(candles) < 80:
        return {"trades": 0, "wins": 0, "winRate": 0.0, "expectancyR": 0.0}
    split = max(40, int(len(candles) * 0.70))
    rewards: list[float] = []
    i = split
    while i < len(candles) - 2:
        prefix = candles[: i + 1]
        signal = evaluate_signal(prefix, arm)
        if signal["action"] not in {"CE", "PE"}:
            i += 1
            continue
        entry = float(candles[i + 1]["open"])
        stop_distance = max(_atr(prefix) * settings.backtest_atr_stop_mult, entry * 0.001)
        direction = 1 if signal["action"] == "CE" else -1
        reward = None
        exit_index = i + 1
        for j in range(i + 1, min(len(candles), i + 2 + settings.backtest_max_hold_bars)):
            high, low = float(candles[j]["high"]), float(candles[j]["low"])
            if direction == 1:
                stop_hit = low <= entry - stop_distance
                target_hit = high >= entry + stop_distance * settings.reward_risk_ratio
            else:
                stop_hit = high >= entry + stop_distance
                target_hit = low <= entry - stop_distance * settings.reward_risk_ratio
            if stop_hit:
                reward = -1.0
                exit_index = j
                break
            if target_hit:
                reward = settings.reward_risk_ratio
                exit_index = j
                break
            exit_index = j
        if reward is None:
            last_close = float(candles[exit_index]["close"])
            reward = direction * (last_close - entry) / stop_distance
            reward = max(-1.0, min(settings.reward_risk_ratio, reward))
        rewards.append(reward)
        i = exit_index + 1
    wins = sum(1 for r in rewards if r > 0)
    return {
        "trades": len(rewards),
        "wins": wins,
        "winRate": round(wins / len(rewards) * 100, 2) if rewards else 0.0,
        "expectancyR": round(sum(rewards) / len(rewards), 4) if rewards else 0.0,
    }


class AdaptiveLearner:
    def _get_state(self, db) -> StrategyState:
        state = db.scalar(select(StrategyState).where(StrategyState.name == "APEX_ADAPTIVE_V1"))
        if not state:
            state = StrategyState(name="APEX_ADAPTIVE_V1")
            db.add(state); db.commit(); db.refresh(state)
        return state

    def _latest_research(self, db) -> dict:
        row = db.scalar(select(ResearchRun).order_by(ResearchRun.run_date.desc()).limit(1))
        if not row:
            return {}
        try:
            return json.loads(row.results_json or "{}")
        except json.JSONDecodeError:
            return {}

    def snapshot(self, context: str | None = None) -> dict:
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            global_q, global_counts = {}, {}
            for arm in ARMS:
                global_q[arm.name] = float(q_values.get(arm.name, 0.0))
                global_counts[arm.name] = int(counts.get(arm.name, 0))
            contextual = {}
            if context:
                contextual = {arm.name: float(q_values.get(f"{context}::{arm.name}", global_q[arm.name])) for arm in ARMS}
            best = max(ARMS, key=lambda a: global_q[a.name]).name
            return {
                "enabled": settings.adaptive_learning_enabled,
                "method": "contextual epsilon-greedy reinforcement bandit + daily OOS research prior",
                "qValues": global_q,
                "counts": global_counts,
                "contextQ": contextual,
                "bestArm": best,
                "explorationRate": settings.exploration_rate,
                "learningRate": settings.learning_rate,
                "latestResearch": self._latest_research(db),
                "arms": [asdict(a) for a in ARMS],
            }

    def choose_arm(self, context: dict, capital: float) -> StrategyArm:
        available = eligible_arms(capital, context)
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            research = self._latest_research(db).get("arms", {})
        key = context.get("key", "UNKNOWN")
        total = sum(int(counts.get(f"{key}::{arm.name}", 0)) for arm in available)
        if not settings.adaptive_learning_enabled:
            return next((a for a in available if a.name == "APEX_BREAKOUT_BALANCED"), available[0])
        if total < settings.minimum_learning_trades:
            researched = [
                (arm, research.get(arm.name) or {}) for arm in available
                if int((research.get(arm.name) or {}).get("trades", 0)) >= 5
            ]
            if researched:
                best_arm, best_stats = max(researched, key=lambda item: float(item[1].get("expectancyR", 0.0)))
                if float(best_stats.get("expectancyR", 0.0)) > 0:
                    return best_arm
            return next((a for a in available if a.name == "APEX_BREAKOUT_BALANCED"), available[0])
        if random.random() < settings.exploration_rate:
            return random.choice(available)

        def blended(arm: StrategyArm) -> float:
            contextual = float(q_values.get(f"{key}::{arm.name}", q_values.get(arm.name, 0.0)))
            prior = float((research.get(arm.name) or {}).get("expectancyR", 0.0))
            w = max(0.0, min(0.5, settings.research_prior_weight))
            return contextual * (1 - w) + prior * w

        return max(available, key=blended)

    def learn(self) -> dict:
        with SessionLocal() as db:
            state = self._get_state(db)
            q_values = json.loads(state.q_values_json or "{}")
            counts = json.loads(state.counts_json or "{}")
            rows = db.execute(select(Trade).where(Trade.status != "OPEN", Trade.id > state.last_processed_trade_id).order_by(Trade.id.asc())).scalars().all()
            processed = 0
            for trade in rows:
                state.last_processed_trade_id = max(state.last_processed_trade_id, trade.id)
                try:
                    meta = json.loads(trade.reason or "{}")
                except json.JSONDecodeError:
                    continue
                arm = meta.get("strategy")
                context = meta.get("contextKey", "UNKNOWN")
                initial_risk = float(meta.get("initialRisk") or 0)
                if arm not in {a.name for a in ARMS} or initial_risk <= 0:
                    continue
                reward = trade.pnl / initial_risk
                if not isfinite(reward):
                    continue
                reward = max(-2.0, min(2.0, reward))
                for key in (arm, f"{context}::{arm}"):
                    old = float(q_values.get(key, 0.0))
                    q_values[key] = round(old + settings.learning_rate * (reward - old), 6)
                    counts[key] = int(counts.get(key, 0)) + 1
                processed += 1
            if rows:
                state.q_values_json = json.dumps(q_values)
                state.counts_json = json.dumps(counts)
                state.updated_at = datetime.now(timezone.utc)
                db.commit()
            return {"processed": processed, "qValues": q_values, "counts": counts}

    def daily_research(self, provider) -> dict:
        if not settings.daily_research_enabled or not provider.market_ready:
            return {"status": "disabled"}
        today = datetime.now(IST).date()
        with SessionLocal() as db:
            existing = db.scalar(select(ResearchRun).where(ResearchRun.run_date == today.isoformat()))
            if existing:
                return json.loads(existing.results_json or "{}")
        from_date = today - timedelta(days=settings.backtest_lookback_days)
        aggregate = {arm.name: {"trades": 0, "wins": 0, "rewardSum": 0.0} for arm in ARMS}
        markets = {}
        for name, key in settings.underlying_keys.items():
            candles = provider.historical_candles(key, from_date, today)
            market_result = {}
            for arm in ARMS:
                result = _simulate_arm(candles, arm)
                market_result[arm.name] = result
                agg = aggregate[arm.name]
                agg["trades"] += result["trades"]
                agg["wins"] += result["wins"]
                agg["rewardSum"] += result["expectancyR"] * result["trades"]
            markets[name] = market_result
        arms = {}
        for arm in ARMS:
            agg = aggregate[arm.name]
            trades = agg["trades"]
            arms[arm.name] = {
                "trades": trades,
                "wins": agg["wins"],
                "winRate": round(agg["wins"] / trades * 100, 2) if trades else 0.0,
                "expectancyR": round(agg["rewardSum"] / trades, 4) if trades else 0.0,
            }
        result = {
            "status": "completed",
            "runDate": today.isoformat(),
            "lookbackDays": settings.backtest_lookback_days,
            "note": "Underlying directional OOS research only; not an options-PnL backtest.",
            "arms": arms,
            "markets": markets,
        }
        with SessionLocal() as db:
            db.add(ResearchRun(run_date=today.isoformat(), results_json=json.dumps(result)))
            db.commit()
        return result


adaptive_learner = AdaptiveLearner()
