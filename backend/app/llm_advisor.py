from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .core import settings


class OllamaAdvisor:
    """Unified advisory layer: prefer Ollama when configured, otherwise OpenRouter free router."""

    @property
    def provider(self) -> str:
        if settings.ollama_enabled and settings.ollama_base_url and settings.ollama_model:
            return "ollama"
        if settings.openrouter_api_key:
            return "openrouter"
        return "none"

    @property
    def model(self) -> str:
        if self.provider == "ollama":
            return settings.ollama_model
        if self.provider == "openrouter":
            return settings.openrouter_model
        return ""

    @property
    def configured(self) -> bool:
        return self.provider != "none"

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
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

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are an advisory risk reviewer for a PAPER-TRADING research system. "
            "Use only the supplied structured market snapshot. Do not invent prices or news. "
            "Do not override hard risk controls. Prefer NO TRADE when evidence conflicts. "
            "Return ONLY a compact JSON object with keys bias, confidence, risk, "
            "approveTechnicalSetup, reasons, warnings."
        )

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise
            return json.loads(match.group(0))

    def _ollama_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
        return headers

    def _analyze_ollama(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": settings.ollama_model,
            "stream": False,
            "format": self._schema(),
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps(snapshot, default=str)},
            ],
        }
        with httpx.Client(timeout=max(2.0, settings.ollama_timeout_seconds)) as client:
            response = client.post(
                settings.ollama_base_url.rstrip("/") + "/api/chat",
                headers=self._ollama_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = ((data.get("message") or {}).get("content") or "{}").strip()
        return self._parse_json(content)

    def _analyze_openrouter(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": settings.openrouter_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps(snapshot, default=str)},
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "X-Title": "APEX Algo AI",
        }
        with httpx.Client(timeout=max(3.0, settings.openrouter_timeout_seconds)) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}").strip()
        parsed = self._parse_json(content)
        parsed["routedModel"] = data.get("model")
        return parsed

    def analyze_trade(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {
                "enabled": False,
                "status": "not_configured",
                "provider": "none",
                "bias": "NEUTRAL",
                "confidence": 0.0,
                "risk": "UNKNOWN",
                "approveTechnicalSetup": True,
                "reasons": [],
                "warnings": [],
            }
        try:
            parsed = (
                self._analyze_ollama(snapshot)
                if self.provider == "ollama"
                else self._analyze_openrouter(snapshot)
            )
            parsed["enabled"] = True
            parsed["status"] = "ok"
            parsed["provider"] = self.provider
            parsed["model"] = self.model
            return parsed
        except Exception as exc:
            return {
                "enabled": True,
                "status": "error",
                "provider": self.provider,
                "model": self.model,
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
            with httpx.Client(timeout=max(2.0, settings.ollama_timeout_seconds)) as client:
                for query in queries[: settings.news_query_limit]:
                    response = client.post(
                        "https://ollama.com/api/web_search",
                        headers=self._ollama_headers(),
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
