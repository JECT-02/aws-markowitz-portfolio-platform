from __future__ import annotations

from datetime import date

from common.models import DataSource


def _partition(run_date: date) -> str:
    return f"year={run_date.year:04d}/month={run_date.month:02d}/day={run_date.day:02d}"


def bronze_object_key(run_date: date, source: DataSource, symbol: str) -> str:
    """Build the write-once bronze object key for a symbol and run date."""
    return f"{_partition(run_date)}/source={source.value}/symbol={symbol}/bars.json"


def quarantine_object_key(
    run_date: date,
    source: DataSource,
    symbol: str,
    reason_code: str,
) -> str:
    """Build the quarantine object key for a rejected symbol payload."""
    return f"{_partition(run_date)}/source={source.value}/symbol={symbol}/reason={reason_code}.json"
