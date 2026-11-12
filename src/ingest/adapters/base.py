from __future__ import annotations

from typing import Protocol

from common.models import BarFetchRequest, BarSeries


class MarketDataSource(Protocol):
    """Provides adjusted daily bars for a set of symbols."""

    name: str

    def fetch_daily_bars(self, request: BarFetchRequest) -> list[BarSeries]: ...
