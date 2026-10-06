from __future__ import annotations

import json
from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import func, select, update

from .core import SessionLocal, settings
from .models import LearningSample, ModelEvaluation, NeuralModelArtifact

IST = ZoneInfo(settings.timezone)

FEATURE_NAMES = [
    "ema_direction",
    "rsi_direction",
    "atr_pct",
    "volume_ratio",
    "pattern_support",
    "pattern_conflict",
    "bos_aligned",
    "choch_aligned",
    "liquidity_aligned",
    "fvg_aligned",
    "fake_breakout_aligned",
    "is_scalp",
    "rr",
    "direction",
]


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _safe_float(value, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _feature_vector(features: dict, direction: str) -> list[float]:
    side = 1.0 if direction == "CE" else -1.0
    aligned = "BULL" if side > 0 else "BEAR"
    opposing = "BEAR" if side > 0 else "BULL"
    bull = features.get("bullPatterns") or []
    bear = features.get("bearPatterns") or []
    support_patterns = bull if side > 0 else bear
    conflict_patterns = bear if side > 0 else bull
    rr = _safe_float(features.get("rr"), 1.5)
    return [
        _clip(_safe_float(features.get("emaGapPct")) * side / 0.40, -3.0, 3.0),
        _clip((_safe_float(features.get("rsi"), 50.0) - 50.0) * side / 20.0, -3.0, 3.0),
        _clip(_safe_float(features.get("atrPct")) / 0.50, 0.0, 4.0),
        _clip((_safe_float(features.get("volumeRatio"), 1.0) - 1.0) / 1.5, -1.0, 3.0),
        _clip(len(support_patterns) / 4.0, 0.0, 2.0),
        _clip(len(conflict_patterns) / 4.0, 0.0, 2.0),
        1.0 if features.get("bos") == aligned else -0.5 if features.get("bos") == opposing else 0.0,
        1.0 if features.get("choch") == aligned else -0.5 if features.get("choch") == opposing else 0.0,
        1.0 if features.get("liquiditySweep") == aligned else -0.5 if features.get("liquiditySweep") == opposing else 0.0,
        1.0 if features.get("fairValueGap") == aligned else -0.5 if features.get("fairValueGap") == opposing else 0.0,
        1.0 if features.get("fakeBreakout") == aligned else -0.5 if features.get("fakeBreakout") == opposing else 0.0,
        1.0 if str(features.get("setupStyle") or "").upper() == "SCALP" else 0.0,
        _clip(rr / 2.0, 0.25, 2.0),
        side,
    ]


def _signal_features(signal: dict) -> tuple[dict, str] | None:
    direction = str(signal.get("action") or "")
    if direction not in {"CE", "PE"}:
        return None
    price = max(_safe_float(signal.get("underlyingPrice"), 0.0), 1e-9)
    ema_fast = _safe_float(signal.get("emaFast"))
    ema_slow = _safe_float(signal.get("emaSlow"))
    patterns = signal.get("patterns") or {}
    smc = signal.get("smc") or {}
    style = "SCALP" if "SCALP" in str(signal.get("chosenStrategy") or signal.get("strategy") or "").upper() else "SWING"
    f = {
        "emaGapPct": (ema_fast - ema_slow) / price * 100.0,
        "rsi": _safe_float(signal.get("rsi"), 50.0),
        "atrPct": _safe_float(signal.get("atrPct")),
        "volumeRatio": _safe_float(signal.get("volumeRatio"), 1.0),
        "bullPatterns": patterns.get("bullish") or [],
        "bearPatterns": patterns.get("bearish") or [],
        "bos": smc.get("bos"),
        "choch": smc.get("choch"),
        "liquiditySweep": smc.get("liquiditySweep"),
        "fairValueGap": smc.get("fairValueGap"),
        "fakeBreakout": smc.get("fakeBreakout"),
        "setupStyle": style,
        "rr": 1.25 if style == "SCALP" else 1.8,
    }
    return f, direction


def _sigmoid_np(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    y = y.astype(int)
    n_pos = int(y.sum())
    n_neg = int(len(y) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return 0.5
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1, dtype=float)
    pos_sum = float(ranks[y == 1].sum())
    return (pos_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    if len(y) == 0:
        return {"samples": 0, "accuracy": 0.0, "brier": 1.0, "auc": 0.5, "actualWinRate": 0.0, "avgProbability": 0.0}
    pred = (p >= 0.5).astype(int)
    return {
        "samples": int(len(y)),
        "accuracy": round(float((pred == y).mean()), 4),
        "brier": round(float(np.mean((p - y) ** 2)), 4),
        "auc": round(float(_auc(y, p)), 4),
        "actualWinRate": round(float(y.mean()), 4),
        "avgProbability": round(float(p.mean()), 4),
    }


class NeuralModelService:
    """Small real MLP trained on chronological SMC setup outcomes and persisted in Postgres."""

    def _eligible_rows(self) -> list[LearningSample]:
        with SessionLocal() as db:
            return list(db.execute(
                select(LearningSample).where(
                    LearningSample.strategy.in_(["SMC_SCALP_V2", "SMC_SWING_V2"]),
                    LearningSample.outcome.in_(["TARGET", "STOP", "AMBIGUOUS_STOP_FIRST"]),
                ).order_by(LearningSample.setup_time.asc())
            ).scalars().all())

    @staticmethod
    def _arrays(rows: list[LearningSample]) -> tuple[np.ndarray, np.ndarray]:
        x, y = [], []
        for row in rows:
            try:
                features = json.loads(row.features_json or "{}")
                vector = _feature_vector(features, row.direction)
            except Exception:
                continue
            if len(vector) != len(FEATURE_NAMES):
                continue
            x.append(vector)
            y.append(int(row.label))
        return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)

    @staticmethod
    def _forward(x: np.ndarray, weights: dict) -> np.ndarray:
        mean = np.asarray(weights["mean"], dtype=np.float64)
        std = np.asarray(weights["std"], dtype=np.float64)
        w1 = np.asarray(weights["w1"], dtype=np.float64)
        b1 = np.asarray(weights["b1"], dtype=np.float64)
        w2 = np.asarray(weights["w2"], dtype=np.float64)
        b2 = float(weights["b2"])
        xn = (x - mean) / std
        hidden = np.tanh(xn @ w1 + b1)
        return _sigmoid_np(hidden @ w2 + b2).reshape(-1)

    def train_candidate(self) -> dict:
        if not settings.neural_training_enabled:
            return {"status": "disabled", "kind": "neural_mlp"}
        rows = self._eligible_rows()
        x, y = self._arrays(rows)
        minimum = max(200, int(settings.neural_min_labeled_samples))
        if len(y) < minimum:
            return {
                "status": "insufficient_data",
                "kind": "neural_mlp",
                "eligibleSamples": int(len(y)),
                "minimum": minimum,
                "note": "TIMEOUT/censored rows are excluded from neural labels.",
            }
        source_max_sample_id = max((int(row.id) for row in rows), default=0)
        with SessionLocal() as db:
            latest_existing = db.scalar(select(NeuralModelArtifact).order_by(NeuralModelArtifact.id.desc()).limit(1))
        if latest_existing:
            try:
                latest_metrics = json.loads(latest_existing.metrics_json or "{}")
            except json.JSONDecodeError:
                latest_metrics = {}
            if (
                int(latest_existing.trained_samples or 0) == int(len(y))
                and int(latest_metrics.get("sourceMaxSampleId") or 0) == source_max_sample_id
            ):
                return {
                    "status": "up_to_date",
                    "kind": "neural_mlp",
                    "version": latest_existing.version,
                    "role": latest_existing.role,
                    "eligibleSamples": int(len(y)),
                    "sourceMaxSampleId": source_max_sample_id,
                    "note": "No new labeled V2 outcomes since the last neural training run.",
                }

        if int(y.sum()) < 25 or int((1 - y).sum()) < 25:
            return {
                "status": "insufficient_class_balance",
                "kind": "neural_mlp",
                "eligibleSamples": int(len(y)),
                "positive": int(y.sum()),
                "negative": int((1 - y).sum()),
            }

        split1 = int(len(y) * 0.60)
        split2 = int(len(y) * 0.80)
        x_train, y_train = x[:split1], y[:split1]
        x_valid, y_valid = x[split1:split2], y[split1:split2]
        x_test, y_test = x[split2:], y[split2:]

        mean = x_train.mean(axis=0)
        std = x_train.std(axis=0)
        std[std < 1e-6] = 1.0
        xn = (x_train - mean) / std

        rng = np.random.default_rng(42)
        hidden_units = max(8, min(64, int(settings.neural_hidden_units)))
        w1 = rng.normal(0.0, np.sqrt(2.0 / xn.shape[1]), size=(xn.shape[1], hidden_units))
        b1 = np.zeros(hidden_units, dtype=np.float64)
        w2 = rng.normal(0.0, np.sqrt(1.0 / hidden_units), size=(hidden_units, 1))
        b2 = 0.0

        lr = max(0.0005, min(0.1, float(settings.neural_learning_rate)))
        l2 = max(0.0, min(0.05, float(settings.neural_l2)))
        batch_size = max(32, min(2048, int(settings.neural_batch_size)))
        epochs = max(5, min(150, int(settings.neural_epochs)))
        pos_rate = float(y_train.mean())
        pos_weight = 0.5 / max(pos_rate, 1e-6)
        neg_weight = 0.5 / max(1.0 - pos_rate, 1e-6)

        for _ in range(epochs):
            order = rng.permutation(len(y_train))
            for start in range(0, len(order), batch_size):
                idx = order[start:start + batch_size]
                xb = xn[idx]
                yb = y_train[idx].reshape(-1, 1)
                h = np.tanh(xb @ w1 + b1)
                p = _sigmoid_np(h @ w2 + b2)
                sw = np.where(yb > 0.5, pos_weight, neg_weight)
                dz2 = (p - yb) * sw / max(1, len(idx))
                dw2 = h.T @ dz2 + l2 * w2
                db2 = float(dz2.sum())
                dh = dz2 @ w2.T
                dz1 = dh * (1.0 - h * h)
                dw1 = xb.T @ dz1 + l2 * w1
                db1 = dz1.sum(axis=0)
                np.clip(dw1, -5.0, 5.0, out=dw1)
                np.clip(dw2, -5.0, 5.0, out=dw2)
                w1 -= lr * dw1
                b1 -= lr * db1
                w2 -= lr * dw2
                b2 -= lr * db2

        weights = {
            "mean": mean.tolist(),
            "std": std.tolist(),
            "w1": w1.tolist(),
            "b1": b1.tolist(),
            "w2": w2.tolist(),
            "b2": float(b2),
        }
        train_p = self._forward(x_train, weights)
        valid_p = self._forward(x_valid, weights)
        test_p = self._forward(x_test, weights)
        result = {
            "status": "trained",
            "kind": "neural_mlp",
            "architecture": f"{len(FEATURE_NAMES)}-{hidden_units}-1 tanh/sigmoid",
            "epochs": epochs,
            "eligibleSamples": int(len(y)),
            "excludedTimeouts": int(max(0, len(rows) - len(y))),
            "train": _metrics(y_train, train_p),
            "validation": _metrics(y_valid, valid_p),
            "outOfSample": _metrics(y_test, test_p),
            "featureNames": FEATURE_NAMES,
            "sourceMaxSampleId": source_max_sample_id,
        }

        valid_ok = (
            result["validation"]["auc"] >= settings.neural_promotion_min_auc
            and result["validation"]["brier"] <= settings.neural_promotion_max_brier
        )
        test_ok = (
            result["outOfSample"]["auc"] >= settings.neural_promotion_min_auc
            and result["outOfSample"]["brier"] <= settings.neural_promotion_max_brier
        )

        with SessionLocal() as db:
            production = db.scalar(select(NeuralModelArtifact).where(
                NeuralModelArtifact.role == "PRODUCTION",
                NeuralModelArtifact.status == "PROMOTED",
            ).order_by(NeuralModelArtifact.id.desc()).limit(1))
            production_metrics = {}
            if production:
                try:
                    production_metrics = json.loads(production.metrics_json or "{}")
                except json.JSONDecodeError:
                    production_metrics = {}
            beats_current = True
            if production_metrics:
                old_test = production_metrics.get("outOfSample") or {}
                beats_current = (
                    result["outOfSample"]["auc"] >= float(old_test.get("auc", 0.5)) - 0.01
                    and result["outOfSample"]["brier"] <= float(old_test.get("brier", 1.0)) + 0.01
                )
            promote = bool(valid_ok and test_ok and beats_current)
            version = "nn-" + datetime.now(IST).strftime("%Y%m%d-%H%M%S")
            role = "PRODUCTION" if promote else "CANDIDATE"
            status = "PROMOTED" if promote else "SHADOW"
            if promote:
                db.execute(update(NeuralModelArtifact).where(
                    NeuralModelArtifact.role == "PRODUCTION",
                    NeuralModelArtifact.status == "PROMOTED",
                ).values(role="RETIRED", status="RETIRED"))
            artifact = NeuralModelArtifact(
                version=version,
                role=role,
                status=status,
                architecture=result["architecture"],
                trained_samples=int(len(y)),
                feature_spec_json=json.dumps({"names": FEATURE_NAMES}),
                weights_json=json.dumps(weights),
                metrics_json=json.dumps(result),
            )
            db.add(artifact)
            db.add(ModelEvaluation(
                version=version,
                role=role,
                status=status,
                samples=int(len(y)),
                metrics_json=json.dumps(result),
            ))
            db.commit()
        return {"version": version, "role": role, "promotion": promote, **result}

    def _production(self) -> NeuralModelArtifact | None:
        with SessionLocal() as db:
            return db.scalar(select(NeuralModelArtifact).where(
                NeuralModelArtifact.role == "PRODUCTION",
                NeuralModelArtifact.status == "PROMOTED",
            ).order_by(NeuralModelArtifact.id.desc()).limit(1))

    def predict_signal(self, signal: dict) -> dict:
        parsed = _signal_features(signal)
        if not parsed:
            return {"status": "not_applicable", "probability": None}
        artifact = self._production()
        if artifact is None:
            return {"status": "shadow_waiting", "probability": None, "reason": "no promoted production neural model"}
        features, direction = parsed
        try:
            weights = json.loads(artifact.weights_json or "{}")
            x = np.asarray([_feature_vector(features, direction)], dtype=np.float64)
            p = float(self._forward(x, weights)[0])
        except Exception as exc:
            return {"status": "error", "probability": None, "version": artifact.version, "error": str(exc)[:160]}
        return {
            "status": "production",
            "version": artifact.version,
            "probability": round(p, 4),
            "role": artifact.role,
        }

    def snapshot(self) -> dict:
        with SessionLocal() as db:
            production = db.scalar(select(NeuralModelArtifact).where(
                NeuralModelArtifact.role == "PRODUCTION",
                NeuralModelArtifact.status == "PROMOTED",
            ).order_by(NeuralModelArtifact.id.desc()).limit(1))
            latest = db.scalar(select(NeuralModelArtifact).order_by(NeuralModelArtifact.id.desc()).limit(1))
            total = int(db.scalar(select(func.count()).select_from(NeuralModelArtifact)) or 0)

        def view(row: NeuralModelArtifact | None):
            if row is None:
                return None
            try:
                metrics = json.loads(row.metrics_json or "{}")
            except json.JSONDecodeError:
                metrics = {}
            return {
                "version": row.version,
                "role": row.role,
                "status": row.status,
                "architecture": row.architecture,
                "trainedSamples": row.trained_samples,
                "trainedAt": row.trained_at.isoformat() if row.trained_at else None,
                "metrics": metrics,
            }

        return {
            "enabled": settings.neural_training_enabled,
            "kind": "real_numpy_mlp",
            "automaticTraining": "postmarket/weekend",
            "weightUpdatePolicy": "never during live candles; retrain after labels are known",
            "minimumLabeledSamples": settings.neural_min_labeled_samples,
            "inferenceWeight": settings.neural_inference_weight,
            "modelsTrained": total,
            "production": view(production),
            "latest": view(latest),
        }


neural_model_service = NeuralModelService()
