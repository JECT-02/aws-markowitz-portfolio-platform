from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DataSource(StrEnum):
    """Supported market data sources."""

    ALPACA = "alpaca"
    YFINANCE = "yfinance"


class Bar(BaseModel):
    """Adjusted daily OHLCV bar for a single symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int | None = None
    vwap: float | None = None

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)


class BarSeries(BaseModel):
    """Ordered and unique bar series for a single symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    bars: list[Bar]

    @model_validator(mode="after")
    def _validate_series(self) -> BarSeries:
        timestamps = [bar.timestamp for bar in self.bars]
        if timestamps != sorted(timestamps):
            raise ValueError("bars must be ordered by timestamp")
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("bars must be unique by timestamp")
        if any(bar.symbol != self.symbol for bar in self.bars):
            raise ValueError("bars must match the series symbol")
        return self


class MarketCalendarDay(BaseModel):
    """Trading day entry from the broker calendar."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    open: time
    close: time


class RunMetadata(BaseModel):
    """Metadata attached to every pipeline artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    run_date: date
    code_version: str
    source: DataSource
    fetched_at: datetime

    @field_validator("fetched_at")
    @classmethod
    def _normalize_fetched_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        return value.astimezone(UTC)


class BarFetchRequest(BaseModel):
    """Request for adjusted daily bars."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbols: list[str] = Field(min_length=1)
    start: date
    end: date

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: list[str]) -> list[str]:
        normalized = [symbol.strip().upper() for symbol in value]
        if any(not symbol for symbol in normalized):
            raise ValueError("symbols must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("symbols must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_range(self) -> BarFetchRequest:
        if self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


class RawBarSeries(BaseModel):
    """Canonical raw bar payload returned by a market data source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    payload: list[dict[str, object]]


class QuarantineRecord(BaseModel):
    """Raw payload rejected by ingestion validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: RunMetadata
    symbol: str
    reason_code: str
    detail: str
    payload: list[dict[str, object]]


class IngestResult(BaseModel):
    """Outcome of an ingestion run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: RunMetadata
    accepted: dict[str, str]
    quarantined: dict[str, str]


class CleanResult(BaseModel):
    """Outcome of a silver layer cleaning run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: RunMetadata
    used: list[str]
    missing: list[str]
    excluded: dict[str, str]
    partitions: list[str]
