from __future__ import annotations

from datetime import date

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.models import Calendar
from alpaca.trading.requests import GetCalendarRequest

from common.errors import ExternalSourceError
from common.models import MarketCalendarDay


class TradingCalendar:
    """Trading calendar backed by Alpaca's calendar endpoint."""

    def __init__(self, client: TradingClient) -> None:
        self._client = client

    def trading_days(self, start: date, end: date) -> list[MarketCalendarDay]:
        """Return the exchange trading days between two dates, inclusive."""
        try:
            entries = self._client.get_calendar(GetCalendarRequest(start=start, end=end))
        except APIError as exc:
            raise ExternalSourceError("alpaca calendar request failed") from exc
        days: list[MarketCalendarDay] = []
        for entry in entries:
            if not isinstance(entry, Calendar):
                raise ExternalSourceError("alpaca calendar returned an unexpected payload")
            days.append(
                MarketCalendarDay(
                    date=entry.date,
                    open=entry.open.time(),
                    close=entry.close.time(),
                )
            )
        return days

    def is_trading_day(self, day: date) -> bool:
        """Return whether the exchange is open on the given date."""
        return any(entry.date == day for entry in self.trading_days(day, day))
