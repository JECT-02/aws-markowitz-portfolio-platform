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


def test_maps_alpaca_bars_to_domain_series() -> None:
    client = _FakeClient(_barset())
    source = AlpacaMarketDataSource("key", "secret", client=client)
    series = source.fetch_daily_bars(_request())
    assert len(series) == 1
    assert series[0].symbol == "SPY"
    assert series[0].bars[0].close == pytest.approx(100.5)
    assert series[0].bars[0].trade_count == 10


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
