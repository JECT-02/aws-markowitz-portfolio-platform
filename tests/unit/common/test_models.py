from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from common.models import (
    Bar,
    BarFetchRequest,
    BarSeries,
    DataSource,
    MarketCalendarDay,
    RunMetadata,
)


def _bar(symbol: str = "SPY", day: int = 0, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
    )


def test_bar_normalizes_symbol_and_timestamp() -> None:
    bar = Bar(
        symbol=" spy ",
        timestamp=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=1.0,
        close=2.0,
        volume=10.0,
    )
    assert bar.symbol == "SPY"
    assert bar.timestamp.tzinfo == UTC


def test_bar_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        Bar(
            symbol="SPY",
            timestamp=datetime(2024, 1, 2),
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
        )


def test_bar_rejects_non_finite_price() -> None:
    with pytest.raises(ValidationError):
        _bar(close=float("nan"))


def test_series_rejects_duplicate_timestamps() -> None:
    with pytest.raises(ValidationError):
        BarSeries(symbol="SPY", bars=[_bar(day=0), _bar(day=0)])


def test_series_rejects_unordered_timestamps() -> None:
    with pytest.raises(ValidationError):
        BarSeries(symbol="SPY", bars=[_bar(day=1), _bar(day=0)])


def test_series_rejects_foreign_symbol() -> None:
    with pytest.raises(ValidationError):
        BarSeries(symbol="SPY", bars=[_bar(symbol="QQQ")])


def test_fetch_request_normalizes_symbols() -> None:
    request = BarFetchRequest(
        symbols=["spy", " qqq "],
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
    )
    assert request.symbols == ["SPY", "QQQ"]


def test_fetch_request_rejects_reversed_range() -> None:
    with pytest.raises(ValidationError):
        BarFetchRequest(symbols=["SPY"], start=date(2024, 1, 5), end=date(2024, 1, 1))


def test_run_metadata_normalizes_fetched_at() -> None:
    metadata = RunMetadata(
        run_id="run",
        run_date=date(2024, 1, 2),
        code_version="abc",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
    )
    assert metadata.source is DataSource.ALPACA
    assert metadata.fetched_at.tzinfo == UTC


def test_market_calendar_day_parses_time_strings() -> None:
    entry = MarketCalendarDay.model_validate(
        {"date": "2024-01-02", "open": "09:30", "close": "16:00"}
    )
    assert entry.open.hour == 9
    assert entry.close.hour == 16
