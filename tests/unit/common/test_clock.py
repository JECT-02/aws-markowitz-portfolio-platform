from datetime import timedelta

from common.clock import SystemClock


def test_system_clock_returns_utc_timestamp() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_system_clock_today_matches_now_date() -> None:
    clock = SystemClock()
    assert clock.today() == clock.now().date()
