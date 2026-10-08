from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .core import settings

IST = ZoneInfo(settings.timezone)


class OllamaResearchAssistant:
    @property
    def configured(self) -> bool:
        return bool(
            (settings.ollama_enabled and settings.ollama_base_url and settings.ollama_model)
            or settings.openrouter_api_key
        )

    @property
    def provider(self) -> str:
        if settings.ollama_enabled and settings.ollama_base_url and settings.ollama_model:
            return "ollama"
        if settings.openrouter_api_key:
            return "openrouter"
        return "none"

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                raise
            return json.loads(match.group(0))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
        return headers

    @staticmethod
    def _evidence_freshness(research: dict[str, Any]) -> dict[str, Any]:
        markets = research.get("markets") or ((research.get("analytics") or {}).get("markets") if isinstance(research.get("analytics"), dict) else {}) or {}
        sensex = markets.get("SENSEX") or {}
        frame = (sensex.get("frames") or {}).get("1m") or {}
        raw = frame.get("lastCandleTime")
        max_age = max(60, int(settings.live_trade_candle_max_age_seconds))
        if not raw:
            return {"fresh": False, "state": "MISSING", "ageSeconds": None, "maxAgeSeconds": max_age, "candleTime": None}
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            ts = ts.astimezone(IST)
            age = (datetime.now(IST) - ts).total_seconds()
            fresh = -120 <= age <= max_age
            return {
                "fresh": fresh,
                "state": "LIVE" if fresh else "STALE",
                "ageSeconds": round(age, 1),
                "maxAgeSeconds": max_age,
                "candleTime": ts.isoformat(),
            }
        except Exception:
            return {"fresh": False, "state": "INVALID_TIMESTAMP", "ageSeconds": None, "maxAgeSeconds": max_age, "candleTime": str(raw)}

    def summarize(self, research: dict[str, Any], news: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            return {
                "enabled": False,
                "status": "not_configured",
                "summary": "LLM research layer is not configured.",
                "marketState": "UNKNOWN",
                "riskState": "UNKNOWN",
                "observations": [],
                "sources": [],
            }

        schema = {
            "type": "object",
            "properties": {
                "marketState": {"type": "string", "enum": ["BULLISH", "BEARISH", "MIXED", "NEUTRAL", "UNKNOWN"]},
                "riskState": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"]},
                "summary": {"type": "string"},
                "observations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["marketState", "riskState", "summary", "observations"],
        }
        freshness = self._evidence_freshness(research)
        stale = not bool(freshness.get("fresh"))
        prompt = {
            "marketResearch": research,
            "newsResearch": news or {},
            "evidenceFreshness": freshness,
            "instruction": (
                "Summarize only the supplied evidence for a PAPER-TRADING research dashboard. "
                "Do not recommend a trade, option side, entry, stop, target, leverage, or position size. "
                "If evidence conflicts, say MIXED. Do not invent facts. "
                + (
                    "IMPORTANT: the 1-minute market evidence is stale/unverified. Treat every market observation as a LAST VERIFIED/HISTORICAL snapshot, "
                    "set marketState to UNKNOWN, do not use words like current, firmly, now, presently, or live to describe direction, "
                    "and explicitly state that the present market trend cannot be verified until fresh data arrives."
                    if stale else
                    "The 1-minute evidence is fresh enough for a current research summary, but remain conservative."
                )
            ),
        }
        payload = {
            "model": settings.ollama_model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": "You are a conservative market-research summarizer. Return JSON only."},
                {"role": "user", "content": json.dumps(prompt, default=str)},
            ],
        }
        try:
            if self.provider == "openrouter":
                openrouter_payload = {
                    "model": settings.openrouter_model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": "You are a conservative market-research summarizer. Return JSON only with marketState, riskState, summary, observations."},
                        {"role": "user", "content": json.dumps(prompt, default=str)},
                    ],
                }
                with httpx.Client(timeout=max(3.0, settings.openrouter_timeout_seconds)) as client:
                    response = client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.openrouter_api_key}",
                            "Content-Type": "application/json",
                            "X-Title": "APEX Algo AI",
                        },
                        json=openrouter_payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}")
                parsed = self._parse_json(content)
                parsed["model"] = data.get("model")
            else:
                with httpx.Client(timeout=max(2.0, settings.ollama_timeout_seconds)) as client:
                    response = client.post(
                        settings.ollama_base_url.rstrip("/") + "/api/chat",
                        headers=self._headers(),
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                parsed = self._parse_json(((data.get("message") or {}).get("content") or "{}").strip())
            parsed["enabled"] = True
            parsed["status"] = "ok"
            parsed["provider"] = self.provider
            parsed["freshness"] = freshness
            if stale:
                parsed["marketState"] = "UNKNOWN"
                parsed["riskState"] = "UNKNOWN"
                raw_summary = str(parsed.get("summary") or "").strip()
                parsed["summary"] = (
                    "STALE / HISTORICAL SNAPSHOT — current market direction is unverified. "
                    + raw_summary
                )[:1200]
                parsed["observations"] = [
                    "Live directional interpretation suppressed until a fresh 1-minute candle is available.",
                    *[str(x)[:260] for x in (parsed.get("observations") or [])[:6]],
                ]
            news_rows = (news or {}).get("results", []) if isinstance(news, dict) else (news or [])
            parsed["sources"] = [row.get("url") for row in news_rows if isinstance(row, dict) and row.get("url")]
            return parsed
        except Exception as exc:
            return {
                "enabled": True,
                "status": "error",
                "summary": "LLM research summary failed; deterministic analytics remain available.",
                "marketState": "UNKNOWN",
                "riskState": "UNKNOWN",
                "observations": [str(exc)[:180]],
                "sources": [],
            }

    def news_research(self) -> dict[str, Any]:
        if not (settings.ollama_web_search_enabled and settings.ollama_api_key):
            return {"enabled": False, "results": []}

        queries = [
            "India stock market Nifty Bank Nifty Sensex latest market news",
            "RBI latest policy liquidity rates India markets",
            "Federal Reserve latest decision global markets India impact",
            "GIFT Nifty latest global cues Asian markets",
            "India VIX volatility latest",
            "geopolitical news affecting Indian equity markets today",
        ]
        results: list[dict[str, str]] = []
        try:
            with httpx.Client(timeout=max(2.0, settings.ollama_timeout_seconds)) as client:
                for query in queries[: settings.news_query_limit]:
                    response = client.post(
                        "https://ollama.com/api/web_search",
                        headers=self._headers(),
                        json={"query": query},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    for row in (payload.get("results") or [])[: settings.news_results_per_query]:
                        if not row.get("url"):
                            continue
                        results.append({
                            "title": str(row.get("title") or "")[:220],
                            "url": str(row.get("url")),
                            "content": str(row.get("content") or "")[:700],
                        })
            return {"enabled": True, "results": results}
        except Exception as exc:
            return {"enabled": True, "error": str(exc)[:180], "results": results}


ollama_research = OllamaResearchAssistant()
