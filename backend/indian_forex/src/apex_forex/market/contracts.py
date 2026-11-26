"""Canonical Forex instrument and quote contracts. All timestamps are UTC-aware."""
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetClass(StrEnum):
    FOREX = "FOREX"


class ProductType(StrEnum):
    SPOT = "SPOT"
    CFD = "CFD"


class Instrument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(min_length=1)
    provider: str
    provider_instrument_id: str
    venue: str
    asset_class: AssetClass = AssetClass.FOREX
    product_type: ProductType
    symbol: str
    base_currency: str
    quote_currency: str
    tick_size: float = Field(gt=0)
    lot_size: float = Field(default=1, gt=0)
    timezone: str = "UTC"
    metadata_version: str = "1"


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    event_time: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    provider: str
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_market(self):
        if self.event_time.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("Quote timestamps must be timezone-aware")
        if self.ask < self.bid:
            raise ValueError("Crossed quote rejected: ask is below bid")
        return self

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class Candle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str
    timeframe: str
    start_time: datetime
    end_time: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    source: str
    is_final: bool = False
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ohlc(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("Candle timestamps must be timezone-aware")
        if self.end_time <= self.start_time:
            raise ValueError("Candle end_time must be after start_time")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Invalid candle high")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Invalid candle low")
        return self
