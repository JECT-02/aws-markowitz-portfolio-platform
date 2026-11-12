from datetime import UTC, datetime

import pytest

from common.errors import DataValidationError
from common.models import RawBarSeries
from ingest.core.parsing import parse_series


def _payload(close: float = 100.0) -> list[dict[str, object]]:
    return [
        {
            "symbol": "SPY",
            "timestamp": datetime(2024, 1, 2, tzinfo=UTC),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000.0,
        }
    ]


def test_parses_canonical_payload() -> None:
    series = parse_series(RawBarSeries(symbol="SPY", payload=_payload()))
    assert series.symbol == "SPY"
    assert series.bars[0].close == pytest.approx(100.0)


def test_rejects_naive_timestamp() -> None:
    payload = _payload()
    payload[0]["timestamp"] = datetime(2024, 1, 2)
    with pytest.raises(DataValidationError):
        parse_series(RawBarSeries(symbol="SPY", payload=payload))


def test_rejects_missing_field() -> None:
    payload = _payload()
    payload[0].pop("close")
    with pytest.raises(DataValidationError):
        parse_series(RawBarSeries(symbol="SPY", payload=payload))


def test_rejects_non_finite_price() -> None:
    payload = _payload()
    payload[0]["close"] = float("nan")
    with pytest.raises(DataValidationError):
        parse_series(RawBarSeries(symbol="SPY", payload=payload))
