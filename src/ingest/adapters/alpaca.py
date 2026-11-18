from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, time
from time import sleep as _default_sleep
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from common.errors import ExternalSourceError
from common.logging import get_logger
from common.models import BarFetchRequest, DataSource, RawBarSeries

logger = get_logger(__name__)

_END_OF_DAY = time(23, 59, 59)

# AGENTS.md 7.4: transient provider failures (429 rate limits, 5xx) are
# retried with exponential backoff and jitter before falling back to the
# secondary source; non-transient errors fall back immediately.
MAX_TRANSIENT_RETRIES = 3
_TRANSIENT_BASE_SECONDS = 2.0
_JITTER_LOW = 0.8
_JITTER_HIGH = 1.2


class AlpacaMarketDataSource:
    """Market data source backed by Alpaca historical bars."""

    name = DataSource.ALPACA.value

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        feed: DataFeed = DataFeed.IEX,
        client: StockHistoricalDataClient | None = None,
        sleeper: Callable[[float], None] | None = None,
        jitter: Callable[[float, float], float] | None = None,
    ) -> None:
        self._client = (
            client
            if client is not None
            else StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        )
        self._feed = feed
        self._sleep = sleeper if sleeper is not None else _default_sleep
        self._jitter: Callable[[float, float], float] = (
            jitter if jitter is not None else random.uniform
        )

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
        barset = self._get_with_backoff(request_params)
        if not isinstance(barset, BarSet):
            raise ExternalSourceError("alpaca market data returned a raw payload")
        return _to_raw_series(barset)

    def _get_with_backoff(self, request_params: StockBarsRequest) -> Any:
        last_transient: APIError | None = None
        for attempt in range(MAX_TRANSIENT_RETRIES + 1):
            try:
                return self._client.get_stock_bars(request_params)
            except APIError as exc:
                if not _is_transient(exc):
                    raise ExternalSourceError("alpaca market data request failed") from exc
                last_transient = exc
                if attempt < MAX_TRANSIENT_RETRIES:
                    delay = _TRANSIENT_BASE_SECONDS * (2**attempt)
                    delay = self._jitter(_JITTER_LOW * delay, _JITTER_HIGH * delay)
                    logger.warning(
                        "transient alpaca error; backing off",
                        extra={"attempt": attempt + 1, "delay_seconds": round(delay, 2)},
                    )
                    self._sleep(delay)
        raise ExternalSourceError(
            "alpaca market data failed after transient retries"
        ) from last_transient


def _status_code(exc: APIError) -> int | None:
    """Best-effort http status from the alpaca-py error shapes."""
    status: int | None = exc.status_code
    if status is None:
        http_error = getattr(exc, "_http_error", None)
        if isinstance(http_error, int):
            status = http_error
    return status


def _is_transient(exc: APIError) -> bool:
    """429 and 5xx responses may succeed after a bounded backoff."""
    status = _status_code(exc)
    return status == 429 or (status is not None and 500 <= status < 600)


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
