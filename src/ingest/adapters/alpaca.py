from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from common.errors import ExternalSourceError
from common.models import BarFetchRequest, DataSource, RawBarSeries

_END_OF_DAY = time(23, 59, 59)


class AlpacaMarketDataSource:
    """Market data source backed by Alpaca historical bars."""

    name = DataSource.ALPACA.value

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        feed: DataFeed = DataFeed.IEX,
        client: StockHistoricalDataClient | None = None,
    ) -> None:
        self._client = (
            client
            if client is not None
            else StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        )
        self._feed = feed

    def fetch_daily_bars(self, request: BarFetchRequest) -> list[RawBarSeries]:
        """Fetch adjusted daily bars for the requested symbols and range."""
        request_params = StockBarsRequest(
            symbol_or_symbols=request.symbols,
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            start=datetime.combine(request.start, time.min, tzinfo=UTC),
            end=datetime.combine(request.end, _END_OF_DAY, tzinfo=UTC),
            adjustment=Adjustment.ALL,
            feed=self._feed,
        )
        try:
            barset = self._client.get_stock_bars(request_params)
        except APIError as exc:
            raise ExternalSourceError("alpaca market data request failed") from exc
        if not isinstance(barset, BarSet):
            raise ExternalSourceError("alpaca market data returned a raw payload")
        return _to_raw_series(barset)


def _to_raw_series(barset: BarSet) -> list[RawBarSeries]:
    series: list[RawBarSeries] = []
    for symbol, raw_bars in barset.data.items():
        payload = [_raw_bar(symbol, raw) for raw in raw_bars]
        series.append(RawBarSeries(symbol=symbol, payload=payload))
    return sorted(series, key=lambda item: item.symbol)


def _raw_bar(symbol: str, raw: Any) -> dict[str, object]:
    return {
        "symbol": symbol,
        "timestamp": raw.timestamp,
        "open": raw.open,
        "high": raw.high,
        "low": raw.low,
        "close": raw.close,
        "volume": raw.volume,
        "trade_count": raw.trade_count,
        "vwap": raw.vwap,
    }
