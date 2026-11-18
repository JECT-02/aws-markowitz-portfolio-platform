from datetime import date

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest

from common.errors import ExternalSourceError
from common.models import BarFetchRequest
from ingest.adapters.alpaca import AlpacaMarketDataSource


class _FakeClient:
    def __init__(self, response: object) -> None:
        self._response = response
        self.requests: list[StockBarsRequest] = []

    def get_stock_bars(self, request_params: StockBarsRequest) -> object:
        self.requests.append(request_params)
        return self._response


def _barset() -> BarSet:
    return BarSet(
        {
            "SPY": [
                {
                    "t": "2024-01-02T21:00:00Z",
                    "o": 100.0,
                    "h": 101.0,
                    "l": 99.0,
                    "c": 100.5,
                    "v": 1000,
                    "n": 10,
                    "vw": 100.2,
                }
            ]
        }
    )


def _request() -> BarFetchRequest:
    return BarFetchRequest(symbols=["SPY"], start=date(2024, 1, 2), end=date(2024, 1, 2))


def test_maps_alpaca_bars_to_canonical_payload() -> None:
    client = _FakeClient(_barset())
    source = AlpacaMarketDataSource("key", "secret", client=client)
    series = source.fetch_daily_bars(_request())
    assert len(series) == 1
    assert series[0].symbol == "SPY"
    assert series[0].payload[0]["close"] == pytest.approx(100.5)
    assert series[0].payload[0]["trade_count"] == pytest.approx(10.0)


def test_request_uses_all_adjustments_and_feed() -> None:
    client = _FakeClient(_barset())
    source = AlpacaMarketDataSource("key", "secret", feed=DataFeed.IEX, client=client)
    source.fetch_daily_bars(_request())
    sent = client.requests[0]
    assert sent.adjustment is Adjustment.ALL
    assert sent.feed is DataFeed.IEX


def test_api_error_is_wrapped() -> None:
    client = _FakeClient(APIError("boom"))
    source = AlpacaMarketDataSource("key", "secret", client=client)
    with pytest.raises(ExternalSourceError):
        source.fetch_daily_bars(_request())


def test_raw_payload_is_rejected() -> None:
    client = _FakeClient({"SPY": []})
    source = AlpacaMarketDataSource("key", "secret", client=client)
    with pytest.raises(ExternalSourceError):
        source.fetch_daily_bars(_request())


class _FlakyClient:
    """Fails the first N calls with the given status, then succeeds."""

    def __init__(self, failures: int, status: int, response: object) -> None:
        self._failures = failures
        self._status = status
        self._response = response
        self.calls = 0

    def get_stock_bars(self, request_params: StockBarsRequest) -> object:
        self.calls += 1
        if self.calls <= self._failures:
            raise APIError("transient", self._status)
        return self._response


def _no_jitter(low: float, high: float) -> float:
    assert high >= low
    return low


def test_transient_rate_limit_is_retried_with_backoff() -> None:
    """N-28 / AGENTS.md 7.4: 429 retries with exponential backoff + jitter."""
    sleeps: list[float] = []
    client = _FlakyClient(1, 429, _barset())
    source = AlpacaMarketDataSource(
        "key", "secret", client=client, sleeper=sleeps.append, jitter=_no_jitter
    )
    series = source.fetch_daily_bars(_request())
    assert series[0].symbol == "SPY"
    assert client.calls == 2
    assert len(sleeps) == 1
    assert 1.6 <= sleeps[0] <= 2.4


def test_transient_5xx_is_retried_then_wrapped() -> None:
    sleeps: list[float] = []
    client = _FlakyClient(4, 503, _barset())
    source = AlpacaMarketDataSource(
        "key", "secret", client=client, sleeper=sleeps.append, jitter=_no_jitter
    )
    with pytest.raises(ExternalSourceError, match="transient retries"):
        source.fetch_daily_bars(_request())
    assert client.calls == 4  # initial + MAX_TRANSIENT_RETRIES
    # exponential backoff: ~2, ~4, ~8 seconds (jitter bounds)
    assert 1.6 <= sleeps[0] <= 2.4
    assert 3.2 <= sleeps[1] <= 4.8
    assert 6.4 <= sleeps[2] <= 9.6


def test_non_transient_error_falls_back_immediately() -> None:
    sleeps: list[float] = []
    client = _FlakyClient(99, 404, _barset())
    source = AlpacaMarketDataSource(
        "key", "secret", client=client, sleeper=sleeps.append, jitter=_no_jitter
    )
    with pytest.raises(ExternalSourceError):
        source.fetch_daily_bars(_request())
    assert client.calls == 1
    assert sleeps == []
