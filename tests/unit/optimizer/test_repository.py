from __future__ import annotations

import io
import json
from datetime import date, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from common.errors import DataValidationError
from common.storage import ObjectStorage
from optimizer.core.repository import SilverReturnsRepository

SCHEMA = pa.schema(
    [
        pa.field("date", pa.date32(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("log_return", pa.float64(), nullable=False),
    ]
)


class _MemoryStorage(ObjectStorage):
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self._objects

    def write_json_if_absent(self, bucket: str, key: str, payload: dict[str, Any]) -> bool:
        if self.exists(bucket, key):
            return False
        self.write_json(bucket, key, payload)
        return True

    def write_json(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        self._objects[(bucket, key)] = json.dumps(payload).encode("utf-8")

    def read_json(self, bucket: str, key: str) -> dict[str, Any]:
        return dict(json.loads(self._objects[(bucket, key)]))

    def write_parquet(self, bucket: str, key: str, table: pa.Table) -> None:
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        self._objects[(bucket, key)] = buffer.getvalue()

    def read_parquet(self, bucket: str, key: str) -> pa.Table:
        return pq.read_table(io.BytesIO(self._objects[(bucket, key)]))

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        return sorted(
            key for name, key in self._objects if name == bucket and key.startswith(prefix)
        )


def _table(rows: list[tuple[date, str, float]]) -> pa.Table:
    return pa.Table.from_pylist(
        [{"date": day, "symbol": symbol, "log_return": value} for day, symbol, value in rows],
        schema=SCHEMA,
    )


def _rows(days: int, symbols: tuple[str, ...] = ("SPY", "QQQ")) -> list[tuple[date, str, float]]:
    start = date(2024, 1, 1)
    rows: list[tuple[date, str, float]] = []
    for offset in range(days):
        for index, symbol in enumerate(symbols):
            rows.append((start + timedelta(days=offset), symbol, 0.001 * (index + 1)))
    return rows


def _repository() -> tuple[_MemoryStorage, SilverReturnsRepository]:
    storage = _MemoryStorage()
    return storage, SilverReturnsRepository(storage, "silver")


def test_loads_aligned_trailing_window() -> None:
    storage, repository = _repository()
    storage.write_parquet("silver", "year=2024/month=01/returns.parquet", _table(_rows(8)))
    matrix = repository.load(["SPY", "QQQ"], window_days=5)
    assert matrix.symbols == ["SPY", "QQQ"]
    assert matrix.n_observations == 5
    assert matrix.values.shape == (5, 2)
    assert matrix.dates == [date(2024, 1, 4) + timedelta(days=offset) for offset in range(5)]
    assert matrix.excluded == {}


def test_missing_symbol_is_excluded() -> None:
    storage, repository = _repository()
    storage.write_parquet("silver", "year=2024/month=01/returns.parquet", _table(_rows(6)))
    matrix = repository.load(["SPY", "QQQ", "IWM"], window_days=5)
    assert matrix.symbols == ["SPY", "QQQ"]
    assert matrix.excluded["IWM"] == "no_data"


def test_insufficient_history_is_excluded() -> None:
    storage, repository = _repository()
    rows = _rows(5, ("SPY",))
    rows += [(date(2024, 1, 1) + timedelta(days=offset), "QQQ", 0.002) for offset in range(2)]
    storage.write_parquet("silver", "year=2024/month=01/returns.parquet", _table(rows))
    matrix = repository.load(["SPY", "QQQ"], window_days=5)
    assert matrix.symbols == ["SPY"]
    assert matrix.excluded["QQQ"] == "insufficient_history"


def test_missing_values_exclude_symbol() -> None:
    storage, repository = _repository()
    rows = _rows(6, ("SPY",))
    rows += [(date(2024, 1, 1) + timedelta(days=offset), "QQQ", 0.002) for offset in range(5)]
    rows.append((date(2024, 1, 6), "QQQ", float("nan")))
    storage.write_parquet("silver", "year=2024/month=01/returns.parquet", _table(rows))
    matrix = repository.load(["SPY", "QQQ"], window_days=5)
    assert matrix.symbols == ["SPY"]
    assert matrix.excluded["QQQ"] == "missing_values"


def test_no_partitions_is_rejected() -> None:
    _, repository = _repository()
    with pytest.raises(DataValidationError):
        repository.load(["SPY"], window_days=5)


def test_no_valid_symbols_is_rejected() -> None:
    storage, repository = _repository()
    storage.write_parquet("silver", "year=2024/month=01/returns.parquet", _table(_rows(2)))
    with pytest.raises(DataValidationError):
        repository.load(["SPY", "QQQ"], window_days=5)


def test_duplicate_observations_are_rejected() -> None:
    storage, repository = _repository()
    rows = _rows(6)
    rows.append((date(2024, 1, 1), "SPY", 0.001))
    storage.write_parquet("silver", "year=2024/month=01/returns.parquet", _table(rows))
    with pytest.raises(DataValidationError):
        repository.load(["SPY", "QQQ"], window_days=5)


def test_missing_columns_are_rejected() -> None:
    storage, repository = _repository()
    storage.write_parquet(
        "silver",
        "year=2024/month=01/returns.parquet",
        pa.table({"date": [date(2024, 1, 1)], "symbol": ["SPY"]}),
    )
    with pytest.raises(DataValidationError):
        repository.load(["SPY"], window_days=5)
