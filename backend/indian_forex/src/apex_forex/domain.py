from datetime import datetime, timezone
from enum import StrEnum
from pydantic import BaseModel, Field, ConfigDict


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class MarketTick(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instrument_key: str
    ltp: float = Field(gt=0)
    exchange_timestamp_ms: int | None = None
    received_at: datetime = Field(default_factory=utc_now)
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, (utc_now() - self.received_at).total_seconds())


class PaperOrderRequest(BaseModel):
    instrument_key: str
    side: Side
    quantity: int = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    target: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    strategy: str = Field(default="manual-paper", min_length=1, max_length=80)


class PaperTrade(BaseModel):
    id: str
    instrument_key: str
    side: Side
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    tick_size: float
    strategy: str
    status: str = "OPEN"
    exit_price: float | None = None
    gross_pnl: float = 0
    fees: float = 0
    net_pnl: float = 0
    opened_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None
