from __future__ import annotations

import json
import re
import time
import threading
from datetime import datetime, timezone
from typing import Any

import httpx

from .core import settings


class OllamaAdvisor:
    _rate_lock = threading.Lock()
    _rate_limited_until = 0.0
    _last_429_message = 'OpenRouter rate limit active; retrying automatically after cooldown.'

    """Unified advisory layer: prefer Ollama when configured, otherwise OpenRouter free router."""

    def __init__(self) -> None:
        self._stats_lock = threading.Lock()
        self._last_status = "not_called"
        self._last_error = ""
        self._last_call_at: str | None = None
        self._last_success_at: str | None = None
        self._call_count = 0
        self._success_count = 0
        self._rate_limit_count = 0

    def _record_result(self, result: dict[str, Any], *, attempted: bool = True) -> dict[str, Any]:
        status = str(result.get("status") or "unknown")
        with self._stats_lock:
            self._last_status = status
            self._last_call_at = datetime.now(timezone.utc).isoformat()
            self._last_error = str((result.get("warnings") or [""])[0] or "")[:220] if status != "ok" else ""
            if attempted:
                self._call_count += 1
            if status == "ok":
                self._success_count += 1
                self._last_success_at = self._last_call_at
            if status == "rate_limited":
                self._rate_limit_count += 1
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "configured": self.configured,
                "provider": self.provider,
                "model": self.model,
                "lastStatus": self._last_status if self.configured else "not_configured",
                "lastError": self._last_error,
                "lastCallAt": self._last_call_at,
                "lastSuccessAt": self._last_success_at,
                "calls": self._call_count,
                "successes": self._success_count,
                "rateLimits": self._rate_limit_count,
                "cooldownRemainingSeconds": max(0, int(self._rate_limited_until - time.monotonic() + 0.999)),
            }

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
                "diagnosis": {"type": "string"},
                "rootCause": {"type": "string"},
                "stopAssessment": {"type": "string", "enum": ["TOO_TIGHT", "STRUCTURE_ALIGNED", "TOO_WIDE", "UNKNOWN"]},
                "entryAssessment": {"type": "string", "enum": ["CONFIRMED", "LATE", "PREMATURE", "CONFLICTED", "UNKNOWN"]},
                "executionAssessment": {"type": "string", "enum": ["NORMAL", "SLIPPAGE_OR_GAP", "COST_EROSION", "QUOTE_ANOMALY", "UNKNOWN"]},
                "lessons": {"type": "array", "items": {"type": "string"}},
                "recommendedChanges": {"type": "array", "items": {"type": "string"}},
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
            "approveTechnicalSetup, reasons, warnings. For phase CLOSED_TRADE_REVIEW, also "
            "return diagnosis, rootCause, stopAssessment, entryAssessment, executionAssessment, "
            "lessons and recommendedChanges. Distinguish a genuinely tight stop from a gap, "
            "spread, delayed quote or slippage. If the supplied evidence cannot prove the cause, "
            "say UNKNOWN. Recommendations are research hypotheses only and must never change "
            "hard risk limits or execute trades."
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
            return self._record_result({"enabled": False, "status": "not_configured", "provider": "none", "bias": "NEUTRAL", "confidence": 0.0, "risk": "UNKNOWN", "approveTechnicalSetup": True, "reasons": [], "warnings": [], "authority": "ADVISORY_ONLY"}, attempted=False)
        if self.provider == "openrouter" and time.monotonic() < self._rate_limited_until:
            return self._record_result({"enabled": True, "status": "rate_limited", "provider": self.provider, "model": self.model, "bias": "NEUTRAL", "confidence": 0.0, "risk": "UNKNOWN", "approveTechnicalSetup": True, "reasons": [], "warnings": [self._last_429_message], "authority": "ADVISORY_ONLY"}, attempted=False)
        try:
            parsed = self._analyze_ollama(snapshot) if self.provider == "ollama" else self._analyze_openrouter(snapshot)
            parsed.update({"enabled": True, "status": "ok", "provider": self.provider, "model": self.model, "authority": "ADVISORY_ONLY"})
            return self._record_result(parsed)
        except Exception as exc:
            detail = str(exc)[:180]
            status = "error"
            if isinstance(exc, httpx.HTTPStatusError):
                code = exc.response.status_code
                if code == 401:
                    status, detail = "auth_error", "OpenRouter rejected OPENROUTER_API_KEY (HTTP 401). Update the key in Render Environment."
                elif code == 402:
                    status, detail = "billing_error", "OpenRouter returned HTTP 402: check account credits or provider billing."
                elif code == 429:
                    status = "rate_limited"
                    retry_after = exc.response.headers.get("Retry-After", "")
                    try:
                        cooldown = min(600.0, max(60.0, float(retry_after)))
                    except (TypeError, ValueError):
                        cooldown = 120.0
                    detail = "OpenRouter rate limit (429). APEX pauses LLM calls for " + str(int(cooldown)) + "s; technical scanning and deterministic risk checks continue."
                    with self._rate_lock:
                        self._rate_limited_until = time.monotonic() + cooldown
                        self._last_429_message = detail
            return self._record_result({"enabled": True, "status": status, "provider": self.provider, "model": self.model, "bias": "NEUTRAL", "confidence": 0.0, "risk": "UNKNOWN", "approveTechnicalSetup": True, "reasons": [], "warnings": [detail], "authority": "ADVISORY_ONLY"})

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
