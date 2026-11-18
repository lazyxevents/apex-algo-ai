from __future__ import annotations

import json
from collections import defaultdict
from math import isfinite
from typing import Any

from sqlalchemy import select

from .core import SessionLocal
from .models import Trade


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _first(mapping: dict, *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _nested(mapping: dict, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = mapping
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        if current not in (None, ""):
            return current
    return None


def _instrument(symbol: str, meta: dict) -> str:
    explicit = _first(meta, "index", "underlyingIndex", "indexName", "instrument")
    if explicit:
        value = str(explicit).upper().replace(" ", "")
        if "BANKNIFTY" in value:
            return "BANKNIFTY"
        if "NIFTY" in value:
            return "NIFTY"
        if "SENSEX" in value or "BSE" in value:
            return "SENSEX"
    value = str(symbol or "").upper()
    if value.startswith("SIM|"):
        parts = value.split("|")
        value = "|".join(parts[1:3])
    if "BANKNIFTY" in value or "BANK_NIFTY" in value:
        return "BANKNIFTY"
    if "SENSEX" in value or value.startswith("BSE_FO"):
        return "SENSEX"
    if "NIFTY" in value or value.startswith("NSE_FO"):
        return "NIFTY"
    return "OTHER"


def _regime(meta: dict) -> str:
    value = _nested(
        meta,
        ("marketIntelligence", "regime"),
        ("marketContext", "regime"),
        ("context", "regime"),
        ("regime",),
    )
    if isinstance(value, dict):
        value = value.get("label") or value.get("name") or value.get("state")
    return str(value or "UNKNOWN").upper().replace(" ", "_")


def _strategy(meta: dict) -> str:
    return str(_first(meta, "chosenStrategy", "strategy", "strategyName") or "UNKNOWN")


def _metrics(rows: list[dict]) -> dict:
    pnls = [float(row["pnl"]) for row in rows if isfinite(float(row["pnl"]))]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = 0.0
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    count = len(pnls)
    return {
        "trades": count,
        "wins": len(wins),
        "losses": len(losses),
        "winRatePct": round(len(wins) / count * 100, 2) if count else 0.0,
        "netPnl": round(sum(pnls), 2),
        "expectancy": round(sum(pnls) / count, 2) if count else 0.0,
        "averageWin": round(gross_win / len(wins), 2) if wins else 0.0,
        "averageLoss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profitFactor": round(gross_win / gross_loss, 3) if gross_loss else (999.0 if gross_win else 0.0),
        "maxDrawdown": round(max_drawdown, 2),
        "sampleQuality": "LOW_SAMPLE" if count < 30 else "EARLY_SAMPLE" if count < 100 else "USABLE_SAMPLE",
    }


def build_scorecard(rows: list[dict]) -> dict:
    """Summarize closed, valid trades only; no predictive claims or fabricated probabilities."""
    by_index: dict[str, list[dict]] = defaultdict(list)
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    by_regime: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_index[row["index"]].append(row)
        by_strategy[row["strategy"]].append(row)
        by_regime[row["regime"]].append(row)

    def ranked(groups: dict[str, list[dict]]) -> list[dict]:
        result = [{"name": name, **_metrics(items)} for name, items in groups.items()]
        return sorted(result, key=lambda item: (item["expectancy"], item["profitFactor"]), reverse=True)

    total = _metrics(rows)
    total["trades"] = len(rows)
    return {
        "summary": total,
        "byIndex": ranked(by_index),
        "byStrategy": ranked(by_strategy),
        "byRegime": ranked(by_regime),
        "recentTrades": rows[-12:][::-1],
        "dataQuality": {
            "closedValidTrades": len(rows),
            "excludedInvalidContracts": "INVALID_CONTRACT rows are excluded",
            "regimeCoverage": sum(1 for row in rows if row["regime"] != "UNKNOWN"),
            "strategyCoverage": sum(1 for row in rows if row["strategy"] != "UNKNOWN"),
            "warning": "Historical outcomes describe this recorded sample; they are not a guarantee or a calibrated probability of the next trade.",
        },
    }


class IntelligenceService:
    def snapshot(self) -> dict:
        with SessionLocal() as db:
            trades = list(db.execute(
                select(Trade)
                .where(Trade.status.notin_([ "OPEN", "INVALID_CONTRACT" ]))
                .order_by(Trade.id.asc())
            ).scalars().all())
            rows: list[dict] = []
            for trade in trades:
                meta = _json_object(trade.reason)
                pnl = float(trade.pnl or 0.0)
                rows.append({
                    "id": int(trade.id),
                    "symbol": str(trade.symbol or ""),
                    "index": _instrument(trade.symbol, meta),
                    "side": str(trade.direction or ""),
                    "strategy": _strategy(meta),
                    "regime": _regime(meta),
                    "pnl": pnl,
                    "status": str(trade.status or ""),
                    "closedAt": trade.closed_at.isoformat() if trade.closed_at else None,
                })
            return build_scorecard(rows)


intelligence_service = IntelligenceService()
