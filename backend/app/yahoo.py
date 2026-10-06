from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

import yfinance as yf

from .core import settings

IST = ZoneInfo(settings.timezone)


class YahooFinanceService:
    supports_option_chain = False
    synthetic_paper = True

    @property
    def market_ready(self) -> bool:
        return True

    @property
    def sandbox_ready(self) -> bool:
        return False

    def status(self) -> dict:
        return {
            "provider": "yfinance",
            "marketDataConfigured": True,
            "tokenRequired": False,
            "supportsOptionChain": False,
            "syntheticPaper": True,
            "paperBroker": "internal",
            "note": "Token-free Yahoo index candles; option premiums are simulated for demo paper testing.",
        }

    @staticmethod
    def _interval_for_minutes(minutes: int) -> str:
        minutes = int(minutes)
        allowed = {1, 2, 5, 15, 30, 60, 90}
        if minutes not in allowed:
            raise ValueError(f"Unsupported Yahoo candle interval: {minutes}m")
        return f"{minutes}m"

    @classmethod
    def _interval(cls) -> str:
        return cls._interval_for_minutes(settings.candle_interval_minutes)

    @staticmethod
    def _frame_to_candles(frame) -> list[dict]:
        if frame is None or frame.empty:
            return []
        rows: list[dict] = []
        for ts, row in frame.iterrows():
            try:
                dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IST)
                dt = dt.astimezone(IST)
                open_ = float(row["Open"])
                high = float(row["High"])
                low = float(row["Low"])
                close = float(row["Close"])
                raw_volume = float(row.get("Volume", 0) or 0)
            except (TypeError, ValueError, KeyError):
                continue
            if not all(isfinite(v) and v > 0 for v in (open_, high, low, close)):
                continue
            volume = raw_volume if isfinite(raw_volume) and raw_volume > 0 else 0.0
            rows.append({
                "timestamp": dt.isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "oi": 0.0,
            })
        return rows

    def intraday_candles(self, instrument_key: str) -> list[dict]:
        return self.intraday_candles_interval(instrument_key, settings.candle_interval_minutes)

    def intraday_candles_interval(self, instrument_key: str, minutes: int) -> list[dict]:
        period = "5d" if int(minutes) <= 5 else settings.yfinance_intraday_period
        frame = yf.Ticker(instrument_key).history(
            period=period,
            interval=self._interval_for_minutes(minutes),
            auto_adjust=False,
            actions=False,
            prepost=False,
        )
        return self._frame_to_candles(frame)

    def historical_candles(self, instrument_key: str, from_date: date, to_date: date) -> list[dict]:
        end = to_date + timedelta(days=1)
        frame = yf.Ticker(instrument_key).history(
            start=from_date.isoformat(),
            end=end.isoformat(),
            interval=self._interval(),
            auto_adjust=False,
            actions=False,
            prepost=False,
        )
        return self._frame_to_candles(frame)

    def candles_fresh(self, candles: list[dict]) -> bool:
        if not candles:
            return False
        try:
            ts = datetime.fromisoformat(str(candles[-1]["timestamp"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
        except (ValueError, TypeError, KeyError):
            return False
        age_seconds = (datetime.now(IST) - ts.astimezone(IST)).total_seconds()
        return -120 <= age_seconds <= max(60, settings.yfinance_max_delay_minutes) * 60

    def quote_ltp(self, instrument_key: str) -> float:
        frame = yf.Ticker(instrument_key).history(
            period="1d",
            interval="1m",
            auto_adjust=False,
            actions=False,
            prepost=False,
        )
        candles = self._frame_to_candles(frame)
        if not candles:
            raise RuntimeError(f"No Yahoo quote returned for {instrument_key}")
        return float(candles[-1]["close"])

    def synthetic_option_candidate(
        self,
        index_name: str,
        underlying_key: str,
        direction: str,
        spot: float,
    ) -> dict:
        premium = max(5.0, spot * settings.yfinance_synthetic_premium_pct / 100)
        step = 50 if index_name == "NIFTY" else 100
        strike = round(spot / step) * step
        lot_size = (
            max(1, int(settings.sensex_lot_size))
            if index_name.upper() == "SENSEX"
            else max(1, int(settings.yfinance_synthetic_lot_size))
        )
        symbol = f"SIM|{index_name}|{direction}|{int(strike)}"
        return {
            "instrumentKey": symbol,
            "strike": float(strike),
            "ltp": round(premium, 2),
            "bid": round(premium, 2),
            "ask": round(premium, 2),
            "volume": 0,
            "oi": 0,
            "delta": float(settings.yfinance_synthetic_delta),
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "iv": 0.0,
            "spreadPct": 0.0,
            "selectionScore": 1.0,
            "lotSize": lot_size,
            "syntheticDemo": True,
            "underlyingKey": underlying_key,
            "underlyingEntry": float(spot),
        }

    def synthetic_option_ltp(self, meta: dict) -> float:
        underlying_key = str(meta["underlyingKey"])
        current_spot = self.quote_ltp(underlying_key)
        entry_spot = float(meta["underlyingEntry"])
        entry_premium = float(meta["syntheticEntryPremium"])
        delta = abs(float(meta.get("delta") or settings.yfinance_synthetic_delta))
        direction = str(meta.get("direction") or "CE").upper()
        directional_move = current_spot - entry_spot
        if direction == "PE":
            directional_move *= -1
        premium = entry_premium + directional_move * delta
        return round(max(0.05, premium), 2)

    def place_sandbox_order(self, instrument_key: str, quantity: int, side: str, tag: str) -> dict:
        return {
            "ok": True,
            "mode": "internal_yfinance_demo",
            "orderId": None,
            "instrument": instrument_key,
            "quantity": int(quantity),
            "side": side,
            "tag": tag,
        }

    def sandbox_exit_with_retry(self, instrument_key: str, quantity: int, tag: str) -> dict:
        return self.place_sandbox_order(instrument_key, quantity, "SELL", tag)


yahoo_service = YahooFinanceService()
