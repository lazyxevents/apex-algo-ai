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
        self._token_problem: str | None = None
        self._last_error: str | None = None
        self._last_success_at: str | None = None

    def _market_token(self) -> str:
        # Analytics Token is read-only and long-lived; ideal for market-data
        # ingestion plus APEX internal PAPER execution.
        return settings.upstox_analytics_token or settings.upstox_access_token

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    @property
    def market_ready(self) -> bool:
        return bool(self._market_token()) and self._token_problem is None

    @property
    def sandbox_ready(self) -> bool:
        return bool(settings.upstox_sandbox_token)

    def status(self) -> dict:
        configured = bool(self._market_token())
        token_type = "analytics" if settings.upstox_analytics_token else ("oauth" if settings.upstox_access_token else "missing")
        token_status = self._token_problem or ("MISSING" if not configured else ("READY" if self._last_success_at else "CONFIGURED"))
        token_action_required = token_status in {"MISSING", "INVALID", "ACCESS_DENIED"}
        token_message = (
            "Generate an Upstox Analytics Token and set UPSTOX_ANALYTICS_TOKEN on the backend."
            if token_status == "MISSING" else
            "Upstox rejected the token (HTTP 401). Generate a fresh Analytics Token and update UPSTOX_ANALYTICS_TOKEN."
            if token_status == "INVALID" else
            "Upstox denied market-data access (HTTP 403). Check Analytics Token access or generate a fresh token."
            if token_status == "ACCESS_DENIED" else
            "Upstox Analytics Token is configured. It is read-only and does not place broker orders."
        )
        return {
            "provider": "upstox",
            "displayName": "Upstox Market Data API",
            "marketDataConfigured": self.market_ready,
            "tokenRequired": True,
            "tokenType": token_type,
            "tokenStatus": token_status,
            "tokenActionRequired": token_action_required,
            "tokenMessage": token_message,
            "tokenRenewal": "not_required_for_analytics_token" if token_type == "analytics" else "daily_oauth",
            "tokenInvalid": self._token_problem in {"INVALID", "ACCESS_DENIED"},
            "readOnlyMarketData": True,
            "supportsOptionChain": True,
            "syntheticPaper": False,
            "sandboxConfigured": self.sandbox_ready,
            "paperBroker": settings.paper_broker,
            "lastSuccessAt": self._last_success_at,
            "lastError": self._last_error,
        }

    def _check_response(self, response: httpx.Response) -> httpx.Response:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            if response.status_code == 401:
                self._token_problem = "INVALID"
                self._last_error = "Upstox rejected the market-data token (HTTP 401)."
            elif response.status_code == 403:
                self._token_problem = "ACCESS_DENIED"
                self._last_error = "Upstox denied market-data access (HTTP 403)."
            else:
                self._last_error = f"Upstox market-data request failed (HTTP {response.status_code})."
            raise
        self._last_success_at = datetime.now(timezone.utc).isoformat()
        self._last_error = None
        self._token_problem = None
        return response

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
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN (recommended) or UPSTOX_ACCESS_TOKEN is missing")
        encoded = quote(instrument_key, safe="")
        url = f"{self.api_v3}/historical-candle/intraday/{encoded}/minutes/{settings.candle_interval_minutes}"
        with httpx.Client(timeout=12) as client:
            res = client.get(url, headers=self._headers(self._market_token()))
            self._check_response(res)
            raw = res.json().get("data", {}).get("candles", [])
        return self._parse_candles(raw)

    def historical_candles(self, instrument_key: str, from_date: date, to_date: date) -> list[dict]:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN (recommended) or UPSTOX_ACCESS_TOKEN is missing")
        encoded = quote(instrument_key, safe="")
        url = (
            f"{self.api_v3}/historical-candle/{encoded}/minutes/{settings.candle_interval_minutes}/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )
        with httpx.Client(timeout=20) as client:
            res = client.get(url, headers=self._headers(self._market_token()))
            self._check_response(res)
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
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN (recommended) or UPSTOX_ACCESS_TOKEN is missing")
        cached = self._contracts_cache.get(underlying_key)
        if cached and cached[0] == date.today():
            return cached[1]
        with httpx.Client(timeout=12) as client:
            res = client.get(
                f"{self.api_v2}/option/contract",
                params={"instrument_key": underlying_key},
                headers=self._headers(self._market_token()),
            )
            self._check_response(res)
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
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN (recommended) or UPSTOX_ACCESS_TOKEN is missing")
        with httpx.Client(timeout=12) as client:
            res = client.get(
                f"{self.api_v2}/option/chain",
                params={"instrument_key": underlying_key, "expiry_date": expiry},
                headers=self._headers(self._market_token()),
            )
            self._check_response(res)
            return res.json().get("data", []) or []

    def quote_ltp(self, instrument_key: str) -> float:
        if not self.market_ready:
            raise RuntimeError("UPSTOX_ANALYTICS_TOKEN (recommended) or UPSTOX_ACCESS_TOKEN is missing")
        with httpx.Client(timeout=12) as client:
            res = client.get(
                f"{self.api_v3}/market-quote/quotes",
                params={"instrument_key": instrument_key},
                headers=self._headers(self._market_token()),
            )
            self._check_response(res)
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
