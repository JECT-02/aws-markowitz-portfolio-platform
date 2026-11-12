from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import pytest

from clean.core.service import CleanService
from common.errors import DataValidationError
from common.models import DataSource, RunMetadata
from ingest.core.keys import bronze_object_key


class _MemoryStorage:
    def __init__(self) -> None:
        self.json_objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.parquet_objects: dict[tuple[str, str], pa.Table] = {}

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.json_objects

    def write_json_if_absent(self, bucket: str, key: str, payload: dict[str, Any]) -> bool:
        if self.exists(bucket, key):
            return False
        self.write_json(bucket, key, payload)
        return True

    def write_json(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        self.json_objects[(bucket, key)] = payload

    def read_json(self, bucket: str, key: str) -> dict[str, Any]:
        return self.json_objects[(bucket, key)]

    def write_parquet(self, bucket: str, key: str, table: pa.Table) -> None:
        self.parquet_objects[(bucket, key)] = table

    def read_parquet(self, bucket: str, key: str) -> pa.Table:
        return self.parquet_objects[(bucket, key)]

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        return sorted(key for bucket_name, key in self.json_objects if bucket_name == bucket)


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-1",
        run_date=date(2024, 1, 2),
        code_version="abc",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 2, 22, 0, tzinfo=UTC),
    )


def _bronze(metadata: RunMetadata, symbol: str, closes: dict[int, float]) -> dict[str, Any]:
    bars = [
        {
            "symbol": symbol,
            "timestamp": datetime(2024, 1, day, 21, 0, tzinfo=UTC).isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000.0,
        }
        for day, close in sorted(closes.items())
    ]
    return {"metadata": metadata.model_dump(mode="json"), "bars": bars}


def _storage(series: dict[str, dict[int, float]]) -> _MemoryStorage:
    storage = _MemoryStorage()
    metadata = _metadata()
    for symbol, closes in series.items():
        key = bronze_object_key(metadata.run_date, metadata.source, symbol)
        storage.write_json("bronze", key, _bronze(metadata, symbol, closes))
    return storage


def test_writes_silver_partition_with_metadata() -> None:
    storage = _storage({"SPY": {2: 100.0, 3: 110.0, 4: 99.0}})
    service = CleanService(storage, "bronze", "silver")
    result = service.run(_metadata(), ["SPY"], 0)
    assert result.partitions == ["year=2024/month=01/returns.parquet"]
    table = storage.parquet_objects[("silver", result.partitions[0])]
    assert table.column_names == ["date", "symbol", "log_return"]
    assert table.num_rows == 2
    assert table.schema.metadata is not None
    assert table.schema.metadata[b"run_id"] == b"run-1"
    assert table.schema.metadata[b"source"] == b"alpaca"
    values = table.column("log_return").to_pylist()
    assert values[0] == pytest.approx(0.09531017980432493)
    assert values[1] == pytest.approx(-0.10536051565782628)


def test_missing_bronze_symbol_is_reported() -> None:
    storage = _storage({"SPY": {2: 100.0, 3: 101.0}})
    service = CleanService(storage, "bronze", "silver")
    result = service.run(_metadata(), ["SPY", "QQQ"], 0)
    assert result.missing == ["QQQ"]
    assert result.used == ["SPY"]


def test_insufficient_history_symbol_is_excluded() -> None:
    storage = _storage({"SPY": {3: 100.0, 4: 101.0}, "QQQ": {2: 100.0, 3: 100.0, 4: 100.0}})
    service = CleanService(storage, "bronze", "silver")
    result = service.run(_metadata(), ["SPY", "QQQ"], 2)
    assert result.excluded == {"SPY": "insufficient_history"}


def test_all_symbols_missing_is_rejected() -> None:
    storage = _MemoryStorage()
    service = CleanService(storage, "bronze", "silver")
    with pytest.raises(DataValidationError):
        service.run(_metadata(), ["SPY"], 0)


def test_invalid_bronze_bars_are_rejected() -> None:
    storage = _MemoryStorage()
    key = bronze_object_key(date(2024, 1, 2), DataSource.ALPACA, "SPY")
    storage.write_json("bronze", key, {"bars": [{"symbol": "SPY"}]})
    service = CleanService(storage, "bronze", "silver")
    with pytest.raises(DataValidationError):
        service.run(_metadata(), ["SPY"], 0)


def test_returns_spanning_months_write_multiple_partitions() -> None:
    storage = _MemoryStorage()
    metadata = _metadata().model_copy(update={"run_date": date(2024, 1, 31)})
    key = bronze_object_key(date(2024, 1, 31), DataSource.ALPACA, "SPY")
    bars = [
        {
            "symbol": "SPY",
            "timestamp": moment.isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1.0,
        }
        for moment, close in (
            (datetime(2024, 1, 30, 21, 0, tzinfo=UTC), 99.0),
            (datetime(2024, 1, 31, 21, 0, tzinfo=UTC), 100.0),
            (datetime(2024, 2, 1, 21, 0, tzinfo=UTC), 101.0),
        )
    ]
    storage.write_json("bronze", key, {"metadata": {}, "bars": bars})
    service = CleanService(storage, "bronze", "silver")
    result = service.run(metadata, ["SPY"], 0)
    assert result.partitions == [
        "year=2024/month=01/returns.parquet",
        "year=2024/month=02/returns.parquet",
    ]
