from datetime import date, time

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.models import Calendar

from common.errors import ExternalSourceError
from ingest.calendar import TradingCalendar


class _FakeClient:
    def __init__(self, entries: list[Calendar] | Exception) -> None:
        self._entries = entries
        self.requests: list[object] = []

    def get_calendar(self, request: object) -> list[Calendar]:
        self.requests.append(request)
        if isinstance(self._entries, Exception):
            raise self._entries
        return self._entries


def _calendar() -> list[Calendar]:
    return [Calendar(date="2024-01-02", open="09:30", close="16:00")]


def test_maps_calendar_entries() -> None:
    calendar = TradingCalendar(_FakeClient(_calendar()))
    days = calendar.trading_days(date(2024, 1, 2), date(2024, 1, 2))
    assert len(days) == 1
    assert days[0].date == date(2024, 1, 2)
    assert days[0].open == time(9, 30)
    assert days[0].close == time(16, 0)


def test_is_trading_day() -> None:
    calendar = TradingCalendar(_FakeClient(_calendar()))
    assert calendar.is_trading_day(date(2024, 1, 2)) is True
    assert calendar.is_trading_day(date(2024, 1, 3)) is False


def test_api_error_is_wrapped() -> None:
    calendar = TradingCalendar(_FakeClient(APIError("boom")))
    with pytest.raises(ExternalSourceError):
        calendar.trading_days(date(2024, 1, 2), date(2024, 1, 2))
