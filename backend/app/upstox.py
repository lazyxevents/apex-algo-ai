from datetime import date, datetime, timezone
from time import sleep
from urllib.parse import quote

import httpx

from .core import settings


class UpstoxService:
    api_v2 = "https://api.upstox.com/v2"
    api_v3 = "https://api.upstox.com/v3"
    sandbox_v3 = "https://api-sandbox.upstox.com/v3"
    supports_option_chain = True
    synthetic_paper = False

    def __init__(self):
        self._contracts_cache: dict[str, tuple[date, list[dict]]] = {}

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    @property
    def market_ready(self) -> bool:
        return bool(settings.upstox_access_token)

    @property
    def sandbox_ready(self) -> bool:
        return bool(settings.upstox_sandbox_token)

    def status(self) -> dict:
        return {
            "provider": "upstox",
            "marketDataConfigured": self.market_ready,
            "tokenRequired": True,
            "supportsOptionChain": True,
            "syntheticPaper": False,
            "sandboxConfigured": self.sandbox_ready,
            "paperBroker": settings.paper_broker,
        }

    @staticmethod
    def _parse_candles(raw: list) -> list[dict]:
        candles = []
        for row in raw or []:
            if len(row) < 6:
                continue
            candles.append({
                "timestamp": str(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5] or 0),
                "oi": float(row[6] or 0) if len(row) > 6 else 0,
            })
        return sorted(candles, key=lambda x: x["timestamp"])

    def intraday_candles(self, instrument_key: str) -> list[dict]:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is missing")
        encoded = quote(instrument_key, safe="")
        url = f"{self.api_v3}/historical-candle/intraday/{encoded}/minutes/{settings.candle_interval_minutes}"
        with httpx.Client(timeout=12) as client:
            res = client.get(url, headers=self._headers(settings.upstox_access_token))
            res.raise_for_status()
            raw = res.json().get("data", {}).get("candles", [])
        return self._parse_candles(raw)

    def historical_candles(self, instrument_key: str, from_date: date, to_date: date) -> list[dict]:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is missing")
        encoded = quote(instrument_key, safe="")
        url = (
            f"{self.api_v3}/historical-candle/{encoded}/minutes/{settings.candle_interval_minutes}/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )
        with httpx.Client(timeout=20) as client:
            res = client.get(url, headers=self._headers(settings.upstox_access_token))
            res.raise_for_status()
            raw = res.json().get("data", {}).get("candles", [])
        return self._parse_candles(raw)

    def candles_fresh(self, candles: list[dict]) -> bool:
        if not candles:
            return False
        raw = str(candles[-1].get("timestamp") or "")
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        age_seconds = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        return -60 <= age_seconds <= max(900, settings.candle_interval_minutes * 60 * 3)

    def option_contracts(self, underlying_key: str) -> list[dict]:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is missing")
        cached = self._contracts_cache.get(underlying_key)
        if cached and cached[0] == date.today():
            return cached[1]
        with httpx.Client(timeout=12) as client:
            res = client.get(
                f"{self.api_v2}/option/contract",
                params={"instrument_key": underlying_key},
                headers=self._headers(settings.upstox_access_token),
            )
            res.raise_for_status()
            rows = res.json().get("data", []) or []
        self._contracts_cache[underlying_key] = (date.today(), rows)
        return rows

    def nearest_expiry(self, underlying_key: str) -> str | None:
        today = date.today()
        expiries = set()
        for row in self.option_contracts(underlying_key):
            value = row.get("expiry")
            if not value:
                continue
            try:
                parsed = date.fromisoformat(str(value)[:10])
            except ValueError:
                continue
            if parsed >= today:
                expiries.add(parsed)
        return min(expiries).isoformat() if expiries else None

    def option_chain(self, underlying_key: str, expiry: str) -> list[dict]:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is missing")
        with httpx.Client(timeout=12) as client:
            res = client.get(
                f"{self.api_v2}/option/chain",
                params={"instrument_key": underlying_key, "expiry_date": expiry},
                headers=self._headers(settings.upstox_access_token),
            )
            res.raise_for_status()
            return res.json().get("data", []) or []

    def quote_ltp(self, instrument_key: str) -> float:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN is missing")
        with httpx.Client(timeout=12) as client:
            res = client.get(
                f"{self.api_v3}/market-quote/quotes",
                params={"instrument_key": instrument_key},
                headers=self._headers(settings.upstox_access_token),
            )
            res.raise_for_status()
            data = res.json().get("data", {}) or {}
        for item in data.values():
            for key in ("last_price", "ltp"):
                if item.get(key) is not None:
                    return float(item[key])
        raise RuntimeError(f"No LTP returned for {instrument_key}")

    def place_sandbox_order(self, instrument_key: str, quantity: int, side: str, tag: str) -> dict:
        if settings.paper_broker != "upstox_sandbox":
            return {"ok": True, "mode": "internal", "orderId": None}
        if not self.sandbox_ready:
            raise RuntimeError("PAPER_BROKER=upstox_sandbox but UPSTOX_SANDBOX_TOKEN is missing")
        payload = {
            "quantity": int(quantity),
            "product": settings.upstox_sandbox_product,
            "validity": "DAY",
            "price": 0,
            "tag": tag[:40],
            "instrument_token": instrument_key,
            "order_type": "MARKET",
            "transaction_type": side,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": True,
            "market_protection": 0,
        }
        with httpx.Client(timeout=15) as client:
            res = client.post(
                f"{self.sandbox_v3}/order/place",
                json=payload,
                headers=self._headers(settings.upstox_sandbox_token),
            )
            body = res.json()
            if res.status_code >= 400:
                raise RuntimeError(f"Upstox sandbox order failed: {body}")
        data = body.get("data") or {}
        return {"ok": True, "mode": "upstox_sandbox", "orderId": data.get("order_id"), "raw": body}

    def cancel_sandbox_order(self, order_id: str | None) -> dict:
        if settings.paper_broker != "upstox_sandbox" or not order_id:
            return {"ok": True, "mode": "internal", "orderId": order_id}
        if not self.sandbox_ready:
            return {"ok": False, "error": "sandbox token missing", "orderId": order_id}
        with httpx.Client(timeout=12) as client:
            res = client.delete(
                f"{self.sandbox_v3}/order/cancel",
                params={"order_id": order_id},
                headers=self._headers(settings.upstox_sandbox_token),
            )
            try:
                body = res.json()
            except Exception:
                body = {"text": res.text}
            return {"ok": res.status_code < 400, "orderId": order_id, "raw": body}

    def sandbox_exit_with_retry(self, instrument_key: str, quantity: int, tag: str) -> dict:
        last_error = None
        attempts = max(1, settings.force_exit_retry_count)
        for attempt in range(1, attempts + 1):
            try:
                result = self.place_sandbox_order(instrument_key, quantity, "SELL", f"{tag}-{attempt}")
                return {**result, "attempts": attempt}
            except Exception as exc:
                last_error = str(exc)
                if attempt < attempts:
                    sleep(max(0.0, settings.force_exit_retry_delay_seconds))
        return {"ok": False, "error": last_error, "attempts": attempts}


upstox_service = UpstoxService()
