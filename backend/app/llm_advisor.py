from __future__ import annotations

import json
from typing import Any

import httpx

from .core import settings


class OllamaAdvisor:
    def __init__(self) -> None:
        self.timeout = max(2.0, settings.ollama_timeout_seconds)

    @property
    def configured(self) -> bool:
        return bool(settings.ollama_enabled and settings.ollama_base_url and settings.ollama_model)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
        return headers

    def analyze_trade(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"enabled": False, "status": "not_configured", "bias": "NEUTRAL", "confidence": 0.0, "risk": "UNKNOWN", "reasons": []}

        schema = {
            "type": "object",
            "properties": {
                "bias": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "risk": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                "approveTechnicalSetup": {"type": "boolean"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["bias", "confidence", "risk", "approveTechnicalSetup", "reasons", "warnings"],
        }
        system = (
            "You are an advisory risk reviewer for a PAPER-TRADING research system. "
            "Use only the supplied structured market snapshot. Do not invent prices or news. "
            "Do not override hard risk controls. Prefer NO TRADE when evidence conflicts. "
            "Return only JSON matching the schema."
        )
        payload = {
            "model": settings.ollama_model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(snapshot, default=str)},
            ],
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    settings.ollama_base_url.rstrip("/") + "/api/chat",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = ((data.get("message") or {}).get("content") or "{}").strip()
                parsed = json.loads(content)
                parsed["enabled"] = True
                parsed["status"] = "ok"
                return parsed
        except Exception as exc:
            return {
                "enabled": True,
                "status": "error",
                "bias": "NEUTRAL",
                "confidence": 0.0,
                "risk": "UNKNOWN",
                "approveTechnicalSetup": True,
                "reasons": [],
                "warnings": [str(exc)[:180]],
            }

    def web_research(self, queries: list[str]) -> dict[str, Any]:
        if not (settings.ollama_web_search_enabled and settings.ollama_api_key):
            return {"enabled": False, "results": []}
        results: list[dict] = []
        try:
            with httpx.Client(timeout=self.timeout) as client:
                for query in queries[: settings.news_query_limit]:
                    response = client.post(
                        "https://ollama.com/api/web_search",
                        headers=self._headers(),
                        json={"query": query},
                    )
                    response.raise_for_status()
                    data = response.json()
                    for row in (data.get("results") or [])[: settings.news_results_per_query]:
                        if row.get("url"):
                            results.append({
                                "title": str(row.get("title") or "")[:220],
                                "url": str(row.get("url")),
                                "content": str(row.get("content") or "")[:600],
                            })
            return {"enabled": True, "results": results}
        except Exception as exc:
            return {"enabled": True, "error": str(exc)[:180], "results": results}


ollama_advisor = OllamaAdvisor()
