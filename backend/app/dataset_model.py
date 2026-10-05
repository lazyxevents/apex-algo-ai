from __future__ import annotations

import json
from datetime import datetime
from math import exp
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .core import SessionLocal, settings
from .market_research import _atr, _ema, _patterns, _rsi, _structure
from .models import LearningSample, ModelEvaluation

IST = ZoneInfo(settings.timezone)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + exp(-max(-30.0, min(30.0, x))))


def _feature_row(candles: list[dict], i: int) -> dict:
    prefix = candles[: i + 1]
    cur = prefix[-1]
    closes = [float(x["close"]) for x in prefix]
    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    atr = _atr(prefix)
    close = float(cur["close"])
    pats = _patterns(prefix)
    smc = _structure(prefix)
    vols = [float(x.get("volume", 0) or 0) for x in prefix[-21:]]
    vol_base = sum(vols[:-1]) / max(1, len(vols[:-1])) if len(vols) > 1 else 0.0
    volume_ratio = vols[-1] / vol_base if vol_base > 0 else 1.0
    return {
        "close": close,
        "ema9": ema9,
        "ema21": ema21,
        "emaGapPct": (ema9 - ema21) / max(close, 1e-9) * 100,
        "rsi": _rsi(closes),
        "atr": atr,
        "atrPct": atr / max(close, 1e-9) * 100,
        "volumeRatio": volume_ratio,
        "bullPatterns": pats["bullish"],
        "bearPatterns": pats["bearish"],
        "bos": smc["bos"],
        "choch": smc["choch"],
        "liquiditySweep": smc["liquiditySweep"],
        "fairValueGap": smc["fairValueGap"],
        "fakeBreakout": smc["fakeBreakout"],
    }


def build_samples(instrument: str, timeframe: str, candles: list[dict], horizon: int = 6, rr: float = 1.5) -> list[dict]:
    rows: list[dict] = []
    if len(candles) < 80:
        return rows
    for i in range(30, len(candles) - horizon):
        f = _feature_row(candles, i)
        atr = float(f["atr"])
        if atr <= 0:
            continue
        for direction in ("CE", "PE"):
            entry = float(f["close"])
            stop = entry - atr if direction == "CE" else entry + atr
            target = entry + atr * rr if direction == "CE" else entry - atr * rr
            future = candles[i + 1:i + 1 + horizon]
            outcome = "TIMEOUT"
            label = 0
            mae = mfe = 0.0
            for bar in future:
                hi, lo = float(bar["high"]), float(bar["low"])
                favorable = hi - entry if direction == "CE" else entry - lo
                adverse = entry - lo if direction == "CE" else hi - entry
                mfe, mae = max(mfe, favorable / atr), max(mae, adverse / atr)
                target_hit = hi >= target if direction == "CE" else lo <= target
                stop_hit = lo <= stop if direction == "CE" else hi >= stop
                if target_hit and stop_hit:
                    outcome = "AMBIGUOUS_STOP_FIRST"
                    break
                if stop_hit:
                    outcome = "STOP"
                    break
                if target_hit:
                    outcome, label = "TARGET", 1
                    break
            rows.append({
                "instrument": instrument, "timeframe": timeframe, "setup_time": candles[i]["timestamp"],
                "direction": direction, "strategy": "SMC_CONTEXT_BASELINE", "features": f,
                "label": label, "outcome": outcome, "mae_r": round(mae, 4), "mfe_r": round(mfe, 4),
                "net_r": rr if label else -1.0 if outcome.startswith("STOP") or outcome.startswith("AMBIGUOUS") else 0.0,
            })
    return rows


class DatasetModelService:
    def ingest(self, provider) -> dict:
        inserted = 0
        for name, key in settings.underlying_keys.items():
            candles = provider.intraday_candles_interval(key, 1) if hasattr(provider, "intraday_candles_interval") else provider.intraday_candles(key)
            samples = build_samples(name, "1m", candles)
            with SessionLocal() as db:
                for s in samples:
                    try:
                        ts = datetime.fromisoformat(str(s["setup_time"]).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    exists = db.scalar(select(LearningSample.id).where(
                        LearningSample.instrument == name, LearningSample.timeframe == "1m",
                        LearningSample.setup_time == ts, LearningSample.direction == s["direction"],
                        LearningSample.strategy == s["strategy"],
                    ))
                    if exists:
                        continue
                    db.add(LearningSample(instrument=name,timeframe="1m",setup_time=ts,direction=s["direction"],
                        strategy=s["strategy"],features_json=json.dumps(s["features"]),label=s["label"],outcome=s["outcome"],
                        mae_r=s["mae_r"],mfe_r=s["mfe_r"],net_r=s["net_r"]))
                    inserted += 1
                db.commit()
        return {"inserted": inserted, **self.snapshot()}

    def evaluate_candidate(self) -> dict:
        with SessionLocal() as db:
            rows = db.execute(select(LearningSample).order_by(LearningSample.setup_time.asc())).scalars().all()
        if len(rows) < 200:
            return {"status":"insufficient_data","samples":len(rows),"minimum":200}
        split1, split2 = int(len(rows)*.60), int(len(rows)*.80)
        train, valid, test = rows[:split1], rows[split1:split2], rows[split2:]
        # Deterministic baseline probability scorer; intentionally not presented as a trained NN.
        def prob(row):
            f=json.loads(row.features_json)
            side=1 if row.direction=="CE" else -1
            score=side*float(f.get("emaGapPct",0))*5 + side*(float(f.get("rsi",50))-50)/20
            score += .45 if f.get("liquiditySweep")==("BULL" if side==1 else "BEAR") else 0
            score += .35 if f.get("choch")==("BULL" if side==1 else "BEAR") else 0
            score += .25 if f.get("fairValueGap")==("BULL" if side==1 else "BEAR") else 0
            return _sigmoid(score)
        def metrics(part):
            probs=[prob(r) for r in part]; labels=[r.label for r in part]
            acc=sum((p>=.5)==bool(y) for p,y in zip(probs,labels))/max(1,len(part))
            brier=sum((p-y)**2 for p,y in zip(probs,labels))/max(1,len(part))
            return {"samples":len(part),"accuracy":round(acc,4),"brier":round(brier,4),"avgProbability":round(sum(probs)/max(1,len(probs)),4),"actualWinRate":round(sum(labels)/max(1,len(labels)),4)}
        result={"status":"evaluated","kind":"deterministic_candidate_baseline","train":metrics(train),"validation":metrics(valid),"outOfSample":metrics(test)}
        version="candidate-"+datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        with SessionLocal() as db:
            db.add(ModelEvaluation(version=version,role="CANDIDATE",status="EVALUATED",samples=len(rows),metrics_json=json.dumps(result)))
            db.commit()
        return {"version":version,**result}

    def snapshot(self) -> dict:
        with SessionLocal() as db:
            total=int(db.scalar(select(func.count(LearningSample.id))) or 0)
            wins=int(db.scalar(select(func.count(LearningSample.id)).where(LearningSample.label==1)) or 0)
            latest=db.execute(select(ModelEvaluation).order_by(ModelEvaluation.id.desc()).limit(1)).scalar_one_or_none()
        return {"datasetSize":total,"positiveLabels":wins,"labelRate":round(wins/total,4) if total else 0.0,
                "latestCandidate": None if not latest else {"version":latest.version,"status":latest.status,"samples":latest.samples,"metrics":json.loads(latest.metrics_json or "{}")}}

dataset_model_service=DatasetModelService()
