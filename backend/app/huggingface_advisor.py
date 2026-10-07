from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .core import settings


class HuggingFaceResearchAdvisor:
    """Optional shadow research reviewer. It never places orders or changes risk/trade gates."""

    @property
    def configured(self) -> bool:
        return bool(settings.huggingface_enabled and settings.hf_token.strip() and settings.huggingface_model.strip())

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": bool(settings.huggingface_enabled),
            "configured": self.configured,
            "provider": "huggingface_inference_providers",
            "model": settings.huggingface_model,
            "role": "shadow research reviewer only; no order/risk authority",
            "endpoint": "router.huggingface.co/v1/chat/completions",
        }

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

    @staticmethod
    def _compact_evidence(analytics: dict[str, Any], research_bundle: dict[str, Any]) -> dict[str, Any]:
        sources = []
        for row in (research_bundle.get("sources") or [])[:8]:
            if not isinstance(row, dict):
                continue
            sources.append({
                "title": str(row.get("title") or "")[:180],
                "category": row.get("category"),
                "summary": str(row.get("summary") or row.get("content") or "")[:420],
            })
        news = []
        for row in (research_bundle.get("news") or [])[:8]:
            if not isinstance(row, dict):
                continue
            news.append({
                "title": str(row.get("title") or "")[:180],
                "summary": str(row.get("summary") or row.get("content") or "")[:420],
            })
        return {
            "session": research_bundle.get("session"),
            "markets": analytics.get("markets") or {},
            "plan": research_bundle.get("plan") or {},
            "sectors": (research_bundle.get("sectors") or [])[:12],
            "sources": sources,
            "news": news,
        }

    def review(self, analytics: dict[str, Any], research_bundle: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {
                **self.snapshot(),
                "status": "not_configured",
                "marketState": "UNKNOWN",
                "riskState": "UNKNOWN",
                "confidence": 0.0,
                "observations": [],
                "warnings": ["HF_TOKEN is not configured; shadow review skipped."],
            }

        evidence = self._compact_evidence(analytics, research_bundle)
        system = (
            "You are a conservative SHADOW market-research reviewer for an Indian index PAPER-TRADING system. "
            "Use only supplied evidence. Never place/recommend an order, never choose CE/PE, and never override "
            "deterministic trade/risk gates. Identify evidence quality, regime, contradictions and missed-data risk. "
            "Return JSON only with marketState, riskState, confidence, observations, warnings."
        )
        payload = {
            "model": settings.huggingface_model,
            "temperature": 0,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(evidence, default=str)[:18000]},
            ],
        }
        try:
            with httpx.Client(timeout=max(3.0, settings.huggingface_timeout_seconds)) as client:
                response = client.post(
                    "https://router.huggingface.co/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.hf_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}")
            parsed = self._parse_json(content)
            return {
                **self.snapshot(),
                "status": "ok",
                "routedModel": data.get("model") or settings.huggingface_model,
                "marketState": str(parsed.get("marketState") or "UNKNOWN")[:32],
                "riskState": str(parsed.get("riskState") or "UNKNOWN")[:32],
                "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.0))),
                "observations": [str(x)[:260] for x in (parsed.get("observations") or [])[:8]],
                "warnings": [str(x)[:260] for x in (parsed.get("warnings") or [])[:8]],
            }
        except Exception as exc:
            return {
                **self.snapshot(),
                "status": "error",
                "marketState": "UNKNOWN",
                "riskState": "UNKNOWN",
                "confidence": 0.0,
                "observations": [],
                "warnings": [str(exc)[:220]],
            }


huggingface_advisor = HuggingFaceResearchAdvisor()
