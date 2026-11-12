from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from common.errors import ExternalSourceError
from common.models import BarFetchRequest, DataSource, RawBarSeries

_EASTERN = ZoneInfo("America/New_York")
_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
_DOWNLOAD_ERRORS = (KeyError, OSError, TypeError, ValueError)


class YFinanceMarketDataSource:
    """Fallback market data source backed by yfinance."""

    name = DataSource.YFINANCE.value

    def __init__(self, downloader: Callable[..., pd.DataFrame] | None = None) -> None:
        self._download: Callable[..., pd.DataFrame] = (
            downloader if downloader is not None else yf.download
        )

    def fetch_daily_bars(self, request: BarFetchRequest) -> list[RawBarSeries]:
        """Fetch adjusted daily bars for the requested symbols and range."""
        return [self._fetch_symbol(symbol, request) for symbol in request.symbols]

    def _fetch_symbol(self, symbol: str, request: BarFetchRequest) -> RawBarSeries:
        try:
            frame = self._download(
                symbol,
                start=request.start.isoformat(),
                end=(request.end + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except _DOWNLOAD_ERRORS as exc:
            raise ExternalSourceError(f"yfinance request failed for {symbol}") from exc
        if not isinstance(frame, pd.DataFrame):
            raise ExternalSourceError(f"yfinance returned an unexpected payload for {symbol}")
        return RawBarSeries(symbol=symbol, payload=_to_payload(symbol, frame))


def _to_payload(symbol: str, frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    selected = _select_symbol(frame, symbol)
    if selected is None:
        return []
    frame = selected
    if any(column not in frame.columns for column in _COLUMNS):
        raise ExternalSourceError(f"yfinance frame for {symbol} is missing OHLCV columns")
    payload: list[dict[str, object]] = []
    for index, row in frame.iterrows():
        payload.append(
            {
                "symbol": symbol,
                "timestamp": _timestamp(index),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
            }
        )
    return payload


def _select_symbol(frame: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame
    if symbol not in frame.columns.get_level_values(0):
        return None
    selected = frame[symbol]
    return selected if isinstance(selected, pd.DataFrame) else None


def _timestamp(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.to_pydatetime().replace(tzinfo=_EASTERN)
    return timestamp.tz_convert(_EASTERN).to_pydatetime()
