from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from uuid import uuid4
from zoneinfo import ZoneInfo

from common.clock import Clock
from common.errors import ConfigurationError

_EASTERN = ZoneInfo("America/New_York")


def resolve_run_date(event: Mapping[str, object], clock: Clock) -> date:
    """Resolve the market run date from the event or the New York clock."""
    value = event.get("run_date")
    if value is None:
        return clock.now().astimezone(_EASTERN).date()
    if not isinstance(value, str):
        raise ConfigurationError("run_date must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError("run_date must be an ISO date string") from exc


def resolve_run_id(event: Mapping[str, object]) -> str:
    """Resolve the run identifier from the event or generate one."""
    value = event.get("run_id")
    if value is None:
        return uuid4().hex
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("run_id must be a non-empty string")
    return value.strip()
