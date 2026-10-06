from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .core import settings


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
                "marketState": {"type": "string", "enum": ["BULLISH", "BEARISH", "MIXED", "NEUTRAL"]},
                "riskState": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"]},
                "summary": {"type": "string"},
                "observations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["marketState", "riskState", "summary", "observations"],
        }
        prompt = {
            "marketResearch": research,
            "newsResearch": news or {},
            "instruction": (
                "Summarize only the supplied evidence for a PAPER-TRADING research dashboard. "
                "Do not recommend a trade, option side, entry, stop, target, leverage, or position size. "
                "If evidence conflicts, say MIXED. Do not invent facts."
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
            parsed["sources"] = [row.get("url") for row in (news or {}).get("results", []) if row.get("url")]
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
