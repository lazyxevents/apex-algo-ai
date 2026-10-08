import csv
from datetime import date, datetime, timedelta
from io import StringIO
from math import isfinite
from threading import RLock
from time import monotonic, sleep
from zoneinfo import ZoneInfo

import httpx

from .core import settings

IST = ZoneInfo(settings.timezone)


class DhanService:
    """DhanHQ v2 market-data adapter with internal PAPER execution.

    Market data is real Dhan data. Exchange orders remain impossible unless the
    separate live-order gates are explicitly enabled in a future LIVE phase.
    """

    api_v2 = "https://api.dhan.co/v2"
    supports_option_chain = True
    synthetic_paper = False

    def __init__(self):
        self._lock = RLock()
        self._contracts_cache: dict[str, tuple[date, list[dict]]] = {}
        self._candle_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
        self._option_chain_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self._account_cache: tuple[float, dict] | None = None
        self._last_success_at: str | None = None
        self._last_error: str | None = None
        self._latest_candle_time: str | None = None

    @property
    def market_ready(self) -> bool:
        return bool(settings.dhan_client_id and settings.dhan_access_token)

    @property
    def sandbox_ready(self) -> bool:
        return False

    @property
    def live_order_ready(self) -> bool:
        return bool(
            self.market_ready
            and settings.execution_broker.strip().lower() == "dhan"
            and settings.allow_live_orders
            and settings.trading_mode.strip().upper() == "LIVE"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": settings.dhan_access_token,
            "client-id": settings.dhan_client_id,
        }

    def _ok(self, candle_time: str | None = None) -> None:
        with self._lock:
            self._last_success_at = datetime.now(IST).isoformat()
            self._last_error = None
            if candle_time:
                self._latest_candle_time = candle_time

    def _fail(self, exc: Exception | str) -> None:
        with self._lock:
            self._last_error = str(exc)[:500]

    @staticmethod
    def _parse_key(instrument_key: str) -> tuple[str, str, str]:
        parts = str(instrument_key or "").split("|")
        if len(parts) != 3:
            raise ValueError(
                f"Invalid Dhan instrument key {instrument_key!r}; expected exchangeSegment|securityId|instrument"
            )
        segment, security_id, instrument = (x.strip() for x in parts)
        if not segment or not security_id or not instrument:
            raise ValueError(f"Incomplete Dhan instrument key: {instrument_key!r}")
        return segment, security_id, instrument

    def status(self) -> dict:
        return {
            "provider": "dhan",
            "displayName": "DhanHQ Data API",
            "marketDataConfigured": self.market_ready,
            "tokenRequired": True,
            "supportsOptionChain": True,
            "syntheticPaper": False,
            "realMarketData": True,
            "paperBroker": settings.paper_broker,
            "executionBroker": settings.execution_broker,
            "liveOrdersAllowed": bool(self.live_order_ready),
            "lastSuccessAt": self._last_success_at,
            "latestCandleTime": self._latest_candle_time,
            "lastError": self._last_error,
            "note": (
                "Real Dhan market data with APEX internal PAPER execution."
                if self.market_ready
                else "Add DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to enable Dhan market data."
            ),
        }

    def execution_status(self) -> dict:
        paper = settings.trading_mode.strip().upper() == "PAPER"
        return {
            "provider": "dhan",
            "connected": self.market_ready,
            "paperExecution": paper,
            "liveOrdersConfigured": bool(settings.allow_live_orders),
            "liveOrdersAllowed": bool(self.live_order_ready),
            "executionBroker": settings.execution_broker,
            "mode": "APEX_PAPER" if paper else settings.trading_mode.strip().upper(),
            "reason": None if self.market_ready else "DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN are not configured",
        }

    def _post(self, path: str, payload: dict, timeout: float = 15.0) -> dict:
        if not self.market_ready:
            raise RuntimeError("DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN are missing")
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.post(f"{self.api_v2}{path}", headers=self._headers(), json=payload)
                try:
                    body = res.json()
                except Exception:
                    body = {"raw": res.text}
                if res.status_code >= 400:
                    raise RuntimeError(f"Dhan API {path} failed ({res.status_code}): {body}")
            if isinstance(body, dict) and str(body.get("status") or "").lower() == "failure":
                raise RuntimeError(f"Dhan API {path} failed: {body}")
            self._ok()
            return body
        except Exception as exc:
            self._fail(exc)
            raise

    def _get(self, path: str, timeout: float = 15.0):
        if not self.market_ready:
            raise RuntimeError("DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN are missing")
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.get(f"{self.api_v2}{path}", headers=self._headers())
                if res.status_code >= 400:
                    try:
                        detail = res.json()
                    except Exception:
                        detail = res.text
                    raise RuntimeError(f"Dhan API {path} failed ({res.status_code}): {detail}")
            self._ok()
            return res
        except Exception as exc:
            self._fail(exc)
            raise

    @staticmethod
    def _timestamp_iso(raw) -> str:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=IST).isoformat()
        value = str(raw or "")
        try:
            if value.replace(".", "", 1).isdigit():
                return datetime.fromtimestamp(float(value), tz=IST).isoformat()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            return parsed.astimezone(IST).isoformat()
        except (TypeError, ValueError, OSError):
            return value

    @classmethod
    def _parse_candle_arrays(cls, body: dict) -> list[dict]:
        data = body.get("data") if isinstance(body.get("data"), dict) and "open" in body["data"] else body
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        volumes = data.get("volume") or []
        timestamps = data.get("timestamp") or []
        oi = data.get("open_interest") or data.get("oi") or []
        count = min(len(opens), len(highs), len(lows), len(closes), len(timestamps))
        candles: list[dict] = []
        for idx in range(count):
            try:
                o, h, l, c = map(float, (opens[idx], highs[idx], lows[idx], closes[idx]))
            except (TypeError, ValueError):
                continue
            if not all(isfinite(value) and value > 0 for value in (o, h, l, c)):
                continue
            try:
                volume = float(volumes[idx] or 0) if idx < len(volumes) else 0.0
            except (TypeError, ValueError):
                volume = 0.0
            try:
                open_interest = float(oi[idx] or 0) if idx < len(oi) else 0.0
            except (TypeError, ValueError):
                open_interest = 0.0
            candles.append({
                "timestamp": cls._timestamp_iso(timestamps[idx]),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": volume,
                "oi": open_interest,
            })
        return sorted(candles, key=lambda row: row["timestamp"])

    def _intraday_request(
        self,
        instrument_key: str,
        from_dt: datetime,
        to_dt: datetime,
        interval: int,
    ) -> list[dict]:
        segment, security_id, instrument = self._parse_key(instrument_key)
        body = self._post(
            "/charts/intraday",
            {
                "securityId": str(security_id),
                "exchangeSegment": segment,
                "instrument": instrument,
                "interval": str(int(interval)),
                "oi": segment.endswith("_FNO"),
                "fromDate": from_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": to_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            },
            timeout=20,
        )
        candles = self._parse_candle_arrays(body)
        if candles:
            self._ok(str(candles[-1].get("timestamp") or ""))
        return candles

    def intraday_candles(self, instrument_key: str) -> list[dict]:
        return self.intraday_candles_interval(instrument_key, int(settings.candle_interval_minutes))

    def intraday_candles_interval(self, instrument_key: str, minutes: int) -> list[dict]:
        minutes = int(minutes)
        if minutes not in {1, 5, 15, 25, 60}:
            raise ValueError(f"Unsupported Dhan candle interval: {minutes}m")
        key = (instrument_key, minutes)
        ttl = 3.0 if minutes == 1 else 8.0
        now_mono = monotonic()
        with self._lock:
            cached = self._candle_cache.get(key)
            if cached and now_mono - cached[0] <= ttl:
                return list(cached[1])

        now = datetime.now(IST)
        start = now - timedelta(days=max(1, min(30, int(settings.dhan_intraday_lookback_days))))
        candles = self._intraday_request(instrument_key, start, now, minutes)
        with self._lock:
            self._candle_cache[key] = (now_mono, candles)
        return list(candles)

    def historical_candles(self, instrument_key: str, from_date: date, to_date: date) -> list[dict]:
        start = datetime.combine(from_date, datetime.min.time(), tzinfo=IST).replace(hour=9, minute=15)
        end = datetime.combine(to_date, datetime.min.time(), tzinfo=IST).replace(hour=15, minute=30)
        return self._intraday_request(instrument_key, start, end, int(settings.candle_interval_minutes))

    def candles_fresh(self, candles: list[dict]) -> bool:
        if not candles:
            return False
        try:
            ts = datetime.fromisoformat(str(candles[-1]["timestamp"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
        except (TypeError, ValueError, KeyError):
            return False
        age_seconds = (datetime.now(IST) - ts.astimezone(IST)).total_seconds()
        return -120 <= age_seconds <= max(60, settings.live_trade_candle_max_age_seconds)

    @staticmethod
    def _normalize_chain_side(raw: dict | None, segment: str) -> dict | None:
        if not raw:
            return None
        security_id = raw.get("security_id")
        if security_id in (None, ""):
            return None
        greeks = raw.get("greeks") or {}
        return {
            "instrument_key": f"{segment}|{security_id}|OPTIDX",
            "market_data": {
                "ltp": raw.get("last_price") or 0,
                "bid_price": raw.get("top_bid_price") or 0,
                "ask_price": raw.get("top_ask_price") or 0,
                "volume": raw.get("volume") or 0,
                "oi": raw.get("oi") or 0,
            },
            "option_greeks": {
                "delta": greeks.get("delta") or 0,
                "gamma": greeks.get("gamma") or 0,
                "theta": greeks.get("theta") or 0,
                "vega": greeks.get("vega") or 0,
                "iv": raw.get("implied_volatility") or 0,
            },
        }

    @staticmethod
    def _option_segment(underlying_key: str) -> str:
        segment, security_id, _ = DhanService._parse_key(underlying_key)
        if segment == "IDX_I" and str(security_id) in {"51", "69"}:
            return "BSE_FNO"
        return "NSE_FNO"

    def _expiry_list(self, underlying_key: str) -> list[str]:
        segment, security_id, _ = self._parse_key(underlying_key)
        body = self._post(
            "/optionchain/expirylist",
            {"UnderlyingScrip": int(security_id), "UnderlyingSeg": segment},
        )
        return [str(value)[:10] for value in (body.get("data") or []) if value]

    def nearest_expiry(self, underlying_key: str) -> str | None:
        today = datetime.now(IST).date()
        expiries: list[date] = []
        for value in self._expiry_list(underlying_key):
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                continue
            if parsed >= today:
                expiries.append(parsed)
        return min(expiries).isoformat() if expiries else None

    def option_chain(self, underlying_key: str, expiry: str) -> list[dict]:
        cache_key = (underlying_key, str(expiry)[:10])
        now_mono = monotonic()
        with self._lock:
            cached = self._option_chain_cache.get(cache_key)
            if cached and now_mono - cached[0] < 3.2:
                return list(cached[1])

        segment, security_id, _ = self._parse_key(underlying_key)
        option_segment = self._option_segment(underlying_key)
        body = self._post(
            "/optionchain",
            {
                "UnderlyingScrip": int(security_id),
                "UnderlyingSeg": segment,
                "Expiry": str(expiry)[:10],
            },
        )
        raw_chain = (body.get("data") or {}).get("oc") or {}
        rows: list[dict] = []
        for strike_text, sides in raw_chain.items():
            try:
                strike = float(strike_text)
            except (TypeError, ValueError):
                continue
            rows.append({
                "strike_price": strike,
                "call_options": self._normalize_chain_side((sides or {}).get("ce"), option_segment),
                "put_options": self._normalize_chain_side((sides or {}).get("pe"), option_segment),
            })
        with self._lock:
            self._option_chain_cache[cache_key] = (now_mono, rows)
        return list(rows)

    @staticmethod
    def _pick(row: dict, *names: str):
        for name in names:
            value = row.get(name)
            if value not in (None, ""):
                return value
        return None

    def _instrument_rows(self, segment: str) -> list[dict]:
        cached = self._contracts_cache.get(segment)
        today = datetime.now(IST).date()
        if cached and cached[0] == today:
            return cached[1]

        res = self._get(f"/instrument/{segment}", timeout=25)
        content_type = str(res.headers.get("content-type") or "").lower()
        if "json" in content_type:
            body = res.json()
            raw = body.get("data") if isinstance(body, dict) else body
            rows = list(raw or [])
        else:
            rows = list(csv.DictReader(StringIO(res.text)))

        normalized: list[dict] = []
        for row in rows:
            sec = self._pick(row, "SECURITY_ID", "SEM_SMST_SECURITY_ID", "securityId")
            if sec in (None, ""):
                continue
            lot_raw = self._pick(row, "LOT_SIZE", "SEM_LOT_UNITS", "lotSize")
            try:
                lot_size = int(float(lot_raw or 0))
            except (TypeError, ValueError):
                lot_size = 0
            instrument = str(self._pick(row, "INSTRUMENT", "SEM_INSTRUMENT_NAME") or "")
            if instrument and instrument not in {"OPTIDX", "OPTSTK"}:
                continue
            name = self._pick(
                row,
                "DISPLAY_NAME",
                "SEM_CUSTOM_SYMBOL",
                "SEM_TRADING_SYMBOL",
                "SYMBOL_NAME",
                "SM_SYMBOL_NAME",
                "tradingSymbol",
            )
            normalized.append({
                "instrument_key": f"{segment}|{sec}|OPTIDX",
                "lot_size": lot_size,
                "minimum_lot": lot_size,
                "trading_symbol": str(name or sec),
                "name": str(name or sec),
            })
        self._contracts_cache[segment] = (today, normalized)
        return normalized

    def option_contracts(self, underlying_key: str) -> list[dict]:
        return self._instrument_rows(self._option_segment(underlying_key))

    def quote_ltp(self, instrument_key: str) -> float:
        segment, security_id, _ = self._parse_key(instrument_key)
        body = self._post("/marketfeed/ltp", {segment: [int(security_id)]})
        data = body.get("data") or {}
        segment_data = data.get(segment) or {}
        item = segment_data.get(str(security_id)) or {}
        value = item.get("last_price")
        if value in (None, ""):
            raise RuntimeError(f"No Dhan LTP returned for {instrument_key}")
        return float(value)

    def profile(self) -> dict:
        return self._get("/profile").json()

    def funds(self) -> dict:
        return self._get("/fundlimit").json()

    def positions(self) -> list[dict]:
        body = self._get("/positions").json()
        if isinstance(body, dict) and "data" in body:
            body = body.get("data")
        return list(body or [])

    def account_snapshot(self) -> dict:
        now_mono = monotonic()
        with self._lock:
            if self._account_cache and now_mono - self._account_cache[0] < 45:
                return dict(self._account_cache[1])
        if not self.market_ready:
            return {
                "configured": False,
                "provider": "dhan",
                "paperMode": settings.trading_mode.strip().upper() == "PAPER",
                "liveOrdersAllowed": False,
            }
        profile = self.profile()
        result = {
            "configured": True,
            "provider": "dhan",
            "paperMode": settings.trading_mode.strip().upper() == "PAPER",
            "liveOrdersAllowed": bool(self.live_order_ready),
            "profile": {
                "dhanClientId": profile.get("dhanClientId"),
                "tokenValidity": profile.get("tokenValidity"),
                "activeSegment": profile.get("activeSegment"),
                "dataPlan": profile.get("dataPlan"),
                "dataValidity": profile.get("dataValidity"),
            },
            "funds": self.funds(),
            "positions": self.positions(),
        }
        with self._lock:
            self._account_cache = (now_mono, result)
        return dict(result)

    def place_sandbox_order(self, instrument_key: str, quantity: int, side: str, tag: str) -> dict:
        return {
            "ok": True,
            "mode": "internal_dhan_data_paper",
            "orderId": None,
            "instrument": instrument_key,
            "quantity": int(quantity),
            "side": side,
            "tag": tag,
        }

    def sandbox_exit_with_retry(self, instrument_key: str, quantity: int, tag: str) -> dict:
        return self.place_sandbox_order(instrument_key, quantity, "SELL", tag)

    def place_live_order(self, instrument_key: str, quantity: int, side: str, correlation_id: str) -> dict:
        """Dormant real-order adapter; current automation never calls this method."""
        if not self.live_order_ready:
            raise RuntimeError(
                "Dhan live order blocked: requires TRADING_MODE=LIVE, ALLOW_LIVE_ORDERS=true and EXECUTION_BROKER=dhan"
            )
        segment, security_id, _ = self._parse_key(instrument_key)
        payload = {
            "dhanClientId": settings.dhan_client_id,
            "correlationId": str(correlation_id)[:30],
            "transactionType": str(side).upper(),
            "exchangeSegment": segment,
            "productType": settings.dhan_product_type,
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(quantity),
            "disclosedQuantity": 0,
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }
        return self._post("/orders", payload)


dhan_service = DhanService()
