"""Authenticated MT5 terminal tick bridge.

The terminal runs the MQL5 Expert Advisor; the cloud API only receives market data.
No MT5 order_send/order placement is implemented here.
"""
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from apex_forex.domain import MarketTick


class MT5TickBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._#-]+$")
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    last: float = Field(default=0, ge=0)
    time_msc: int = Field(gt=0)

    def validate_quote(self) -> None:
        if self.ask < self.bid:
            raise ValueError(f"MT5 crossed quote rejected for {self.symbol}")


def normalize_mt5_tick(payload: MT5TickBatch) -> MarketTick:
    payload.validate_quote()
    mid = (payload.bid + payload.ask) / 2
    price = payload.last if payload.last > 0 else mid
    return MarketTick(
        instrument_key=f"MT5:{payload.symbol.upper()}",
        ltp=price,
        bid=payload.bid,
        ask=payload.ask,
        exchange_timestamp_ms=payload.time_msc,
        received_at=datetime.now(timezone.utc),
    )
