from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time for injectable time handling."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC timestamp."""

    def today(self) -> date:
        """Return the current UTC date."""


class SystemClock:
    """Clock backed by the system time."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().date()
