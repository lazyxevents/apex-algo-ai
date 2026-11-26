"""OANDA v20 practice-only Forex market-data adapter.

No order placement methods are intentionally implemented in this module.
"""
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx


class OandaProviderError(RuntimeError):
    pass


INSTRUMENT_RE = re.compile(r"^[A-Z]{3}_[A-Z]{3}$")
GRANULARITIES = {
    "S5", "S10", "S15", "S30", "M1", "M2", "M4", "M5", "M10",
    "M15", "M30", "H1", "H2", "H3", "H4", "H6", "H8", "H12",
    "D", "W", "M",
}


class OandaForexClient:
    PRACTICE_REST = "https://api-fxpractice.oanda.com"
    PRACTICE_STREAM = "https://stream-fxpractice.oanda.com"

    def __init__(
        self, access_token: str, account_id: str = "", environment: str = "practice",
        timeout: float = 15.0,
    ):
        if environment.strip().lower() != "practice":
            raise OandaProviderError("Only OANDA practice environment is enabled")
        self.access_token = access_token.strip()
        self.account_id = account_id.strip()
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise OandaProviderError("OANDA_ACCESS_TOKEN is not configured")
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _account(self, account_id: str | None = None) -> str:
        value = (account_id or self.account_id).strip()
        if not value:
            raise OandaProviderError("OANDA_ACCOUNT_ID is not configured; call /accounts first")
        return value

    @staticmethod
    def validate_instruments(instruments: list[str]) -> list[str]:
        values = list(dict.fromkeys(item.strip().upper() for item in instruments if item.strip()))
        if not values or len(values) > 100:
            raise ValueError("Provide between 1 and 100 Forex instruments")
        invalid = [item for item in values if not INSTRUMENT_RE.fullmatch(item)]
        if invalid:
            raise ValueError(f"Invalid OANDA Forex instrument(s): {', '.join(invalid)}")
        return values

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    f"{self.PRACTICE_REST}{path}", headers=self._headers(), params=params
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                raise OandaProviderError(
                    f"OANDA API returned HTTP {exc.response.status_code}: {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                raise OandaProviderError(f"OANDA request failed: {exc}") from exc
        return response.json()

    async def accounts(self) -> dict:
        return await self._get("/v3/accounts")

    async def account_summary(self, account_id: str | None = None) -> dict:
        return await self._get(f"/v3/accounts/{self._account(account_id)}/summary")

    async def instruments(self, account_id: str | None = None) -> dict:
        return await self._get(f"/v3/accounts/{self._account(account_id)}/instruments")

    async def pricing(self, instruments: list[str], account_id: str | None = None) -> dict:
        values = self.validate_instruments(instruments)
        return await self._get(
            f"/v3/accounts/{self._account(account_id)}/pricing",
            {"instruments": ",".join(values)},
        )

    async def candles(
        self, instrument: str, granularity: str = "M5", count: int = 500,
        price: str = "MBA",
    ) -> dict:
        values = self.validate_instruments([instrument])
        granularity = granularity.upper()
        if granularity not in GRANULARITIES:
            raise ValueError(f"Unsupported OANDA candle granularity: {granularity}")
        if not 1 <= count <= 5000:
            raise ValueError("Candle count must be between 1 and 5000")
        if price not in {"M", "B", "A", "MBA"}:
            raise ValueError("Price component must be M, B, A or MBA")
        return await self._get(
            f"/v3/instruments/{values[0]}/candles",
            {"granularity": granularity, "count": count, "price": price},
        )

    async def stream_prices(
        self, instruments: list[str], account_id: str | None = None,
    ) -> AsyncIterator[dict]:
        values = self.validate_instruments(instruments)
        url = (
            f"{self.PRACTICE_STREAM}/v3/accounts/"
            f"{self._account(account_id)}/pricing/stream"
        )
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream(
                    "GET", url, headers=self._headers(),
                    params={"instruments": ",".join(values)},
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "PRICE":
                            yield event
                        elif event.get("type") == "HEARTBEAT":
                            yield event
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                raise OandaProviderError(
                    f"OANDA stream returned HTTP {exc.response.status_code}: {detail}"
                ) from exc
            except httpx.HTTPError as exc:
                raise OandaProviderError(f"OANDA stream failed: {exc}") from exc

    @staticmethod
    def normalize_price(event: dict) -> dict | None:
        if event.get("type") != "PRICE":
            return None
        bids = event.get("bids") or []
        asks = event.get("asks") or []
        if not bids or not asks:
            return None
        try:
            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            timestamp = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            return None
        if bid <= 0 or ask < bid:
            return None
        return {
            "instrument": event.get("instrument"),
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2,
            "timestamp": timestamp,
            "tradeable": bool(event.get("tradeable", False)),
        }
