from datetime import UTC, datetime, timedelta

import pytest

from common.errors import DataValidationError
from common.models import Bar, BarSeries
from ingest.core.validation import validate_bar_series


def _bar(day: int, close: float, high: float | None = None, low: float | None = None) -> Bar:
    high_price = close if high is None else high
    low_price = close if low is None else low
    return Bar(
        symbol="SPY",
        timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day),
        open=close,
        high=high_price,
        low=low_price,
        close=close,
        volume=1000.0,
    )


def _series(prices: list[float]) -> BarSeries:
    return BarSeries(symbol="SPY", bars=[_bar(index, price) for index, price in enumerate(prices)])


def test_constant_prices_are_valid() -> None:
    validate_bar_series(_series([100.0, 100.0, 100.0]))


def test_empty_series_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        validate_bar_series(BarSeries(symbol="SPY", bars=[]))


def test_non_positive_price_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        validate_bar_series(_series([100.0, 0.0]))


def test_negative_volume_is_rejected() -> None:
    series = BarSeries(
        symbol="SPY",
        bars=[
            Bar(
                symbol="SPY",
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=-1.0,
            )
        ],
    )
    with pytest.raises(DataValidationError):
        validate_bar_series(series)


def test_high_below_low_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        validate_bar_series(BarSeries(symbol="SPY", bars=[_bar(0, 100.0, high=90.0, low=110.0)]))


def test_low_above_close_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        validate_bar_series(BarSeries(symbol="SPY", bars=[_bar(0, 100.0, high=120.0, low=110.0)]))


def test_anomalous_log_return_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        validate_bar_series(_series([100.0, 160.0]))


def test_bounded_log_return_is_accepted() -> None:
    validate_bar_series(_series([100.0, 120.0]))
