from datetime import UTC, date

import pandas as pd
import pytest

from common.errors import ExternalSourceError
from common.models import BarFetchRequest
from ingest.adapters.yfinance import YFinanceMarketDataSource


def _frame(close: float = 100.0) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp("2024-01-02")])
    return pd.DataFrame(
        {
            "Open": [close],
            "High": [close],
            "Low": [close],
            "Close": [close],
            "Volume": [1000.0],
        },
        index=index,
    )


def _request() -> BarFetchRequest:
    return BarFetchRequest(symbols=["SPY"], start=date(2024, 1, 2), end=date(2024, 1, 2))


def test_maps_frame_to_canonical_payload() -> None:
    source = YFinanceMarketDataSource(downloader=lambda *args, **kwargs: _frame(100.5))
    series = source.fetch_daily_bars(_request())
    assert len(series) == 1
    assert series[0].symbol == "SPY"
    assert series[0].payload[0]["close"] == pytest.approx(100.5)
    timestamp = series[0].payload[0]["timestamp"]
    assert isinstance(timestamp, pd.Timestamp) is False
    assert timestamp.tzinfo == UTC


def test_download_uses_exclusive_end_and_adjustments() -> None:
    captured: dict[str, object] = {}

    def fake_download(*args: object, **kwargs: object) -> pd.DataFrame:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _frame()

    source = YFinanceMarketDataSource(downloader=fake_download)
    source.fetch_daily_bars(_request())
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["start"] == "2024-01-02"
    assert kwargs["end"] == "2024-01-03"
    assert kwargs["auto_adjust"] is True


def test_empty_frame_yields_empty_payload() -> None:
    source = YFinanceMarketDataSource(downloader=lambda *args, **kwargs: pd.DataFrame())
    series = source.fetch_daily_bars(_request())
    assert series[0].payload == []


def test_multi_index_frame_selects_symbol() -> None:
    columns = pd.MultiIndex.from_product(
        [["SPY"], ["Open", "High", "Low", "Close", "Volume"]]
    )
    frame = pd.DataFrame(
        [[100.0, 100.0, 100.0, 100.0, 1000.0]],
        columns=columns,
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-02")]),
    )
    source = YFinanceMarketDataSource(downloader=lambda *args, **kwargs: frame)
    series = source.fetch_daily_bars(_request())
    assert series[0].payload[0]["close"] == pytest.approx(100.0)


def test_download_failure_is_wrapped() -> None:
    def fake_download(*args: object, **kwargs: object) -> pd.DataFrame:
        raise ValueError("offline")

    source = YFinanceMarketDataSource(downloader=fake_download)
    with pytest.raises(ExternalSourceError):
        source.fetch_daily_bars(_request())


def test_unexpected_payload_is_rejected() -> None:
    source = YFinanceMarketDataSource(downloader=lambda *args, **kwargs: "not a frame")
    with pytest.raises(ExternalSourceError):
        source.fetch_daily_bars(_request())


def test_missing_column_is_rejected() -> None:
    frame = _frame().drop(columns=["Close"])
    source = YFinanceMarketDataSource(downloader=lambda *args, **kwargs: frame)
    with pytest.raises(ExternalSourceError):
        source.fetch_daily_bars(_request())
