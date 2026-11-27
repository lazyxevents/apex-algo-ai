"""Twelve Data Forex market-data adapter (REST only).

This adapter provides indicative/mid-style prices and OHLC candles. It does not
provide broker bid/ask execution quotes and intentionally has no order methods.
"""
import re
from datetime import datetime, timezone
from typing import Any

import httpx


class TwelveDataProviderError(RuntimeError):
    pass


SYMBOL_RE = re.compile(r"^[A-Z]{3}[/_][A-Z]{3}$")
GRANULARITY_MAP = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D": "1day",
    "W": "1week",
    "M": "1month",
}


class TwelveDataForexClient:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str, timeout: float = 12.0):
        self.api_key = api_key.strip()
        self.timeout = timeout

    def _require_key(self) -> None:
        if not self.api_key:
            raise TwelveDataProviderError("TWELVE_DATA_API_KEY is not configured")

    @staticmethod
    def validate_instruments(instruments: list[str]) -> list[str]:
        values = list(dict.fromkeys(item.strip().upper().replace("-", "/") for item in instruments if item.strip()))
        if not values or len(values) > 8:
            raise ValueError("Provide between 1 and 8 Forex pairs per request")
        invalid = [item for item in values if not SYMBOL_RE.fullmatch(item)]
        if invalid:
            raise ValueError(f"Invalid Twelve Data Forex symbol(s): {', '.join(invalid)}")
        return values

    @staticmethod
    def normalize_symbol(instrument: str) -> str:
        value = instrument.strip().upper().replace("_", "/").replace("-", "/")
        if not SYMBOL_RE.fullmatch(value):
            raise ValueError(f"Invalid Forex symbol: {instrument}")
        return value

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        self._require_key()
        query = {**params, "apikey": self.api_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(f"{self.BASE_URL}/{endpoint}", params=query)
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as exc:
                raise TwelveDataProviderError(
                    f"Twelve Data returned HTTP {exc.response.status_code}"
                ) from exc
            except httpx.HTTPError as exc:
                raise TwelveDataProviderError(f"Twelve Data request failed: {exc}") from exc
            except ValueError as exc:
                raise TwelveDataProviderError("Twelve Data returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise TwelveDataProviderError("Twelve Data returned an unexpected response")
        if payload.get("status") == "error" or payload.get("code"):
            message = str(payload.get("message") or payload.get("code") or "Provider error")
            raise TwelveDataProviderError(f"Twelve Data: {message[:300]}")
        return payload

    async def price(self, instrument: str) -> dict:
        symbol = self.normalize_symbol(instrument)
        payload = await self._get("price", {"symbol": symbol})
        try:
            value = float(payload["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TwelveDataProviderError("Twelve Data price response has no valid price") from exc
        if value <= 0:
            raise TwelveDataProviderError("Twelve Data returned a non-positive price")
        return {
            "symbol": symbol,
            "price": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timestamp_type": "received_at",
            "quote_type": "provider_price_no_bid_ask",
        }

    async def prices(self, instruments: list[str]) -> list[dict]:
        symbols = self.validate_instruments(instruments)
        # Sequential requests respect low-tier per-minute credit limits.
        results = []
        for symbol in symbols:
            results.append(await self.price(symbol))
        return results

    async def candles(self, instrument: str, granularity: str = "M5", count: int = 500) -> dict:
        symbol = self.normalize_symbol(instrument)
        interval = GRANULARITY_MAP.get(granularity.upper())
        if not interval:
            raise ValueError(f"Unsupported Twelve Data timeframe: {granularity}")
        if not 1 <= count <= 5000:
            raise ValueError("Candle count must be between 1 and 5000")
        payload = await self._get(
            "time_series",
            {"symbol": symbol, "interval": interval, "outputsize": count, "format": "JSON"},
        )
        values = payload.get("values")
        if not isinstance(values, list):
            raise TwelveDataProviderError("Twelve Data candle response has no values")
        return {
            "provider": "TWELVE_DATA",
            "symbol": symbol,
            "interval": interval,
            "count": len(values),
            "meta": payload.get("meta", {}),
            "values": values,
            "quote_type": "OHLC_MARKET_DATA",
        }
