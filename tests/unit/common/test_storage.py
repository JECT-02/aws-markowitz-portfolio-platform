from collections.abc import Iterator

import boto3
import pyarrow as pa
import pytest
from moto import mock_aws

from common.errors import StorageError
from common.storage import S3Storage

BUCKET = "lakehouse-test"


@pytest.fixture
def storage() -> Iterator[S3Storage]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3Storage(client)


def test_exists_returns_false_for_missing_key(storage: S3Storage) -> None:
    assert storage.exists(BUCKET, "missing.json") is False


def test_write_json_if_absent_is_idempotent(storage: S3Storage) -> None:
    assert storage.write_json_if_absent(BUCKET, "k.json", {"a": 1}) is True
    assert storage.write_json_if_absent(BUCKET, "k.json", {"a": 2}) is False
    assert storage.read_json(BUCKET, "k.json") == {"a": 1}


def test_write_json_overwrites(storage: S3Storage) -> None:
    storage.write_json(BUCKET, "k.json", {"a": 1})
    storage.write_json(BUCKET, "k.json", {"a": 2})
    assert storage.read_json(BUCKET, "k.json") == {"a": 2}


def test_read_missing_json_raises(storage: S3Storage) -> None:
    with pytest.raises(StorageError):
        storage.read_json(BUCKET, "missing.json")


def test_parquet_roundtrip(storage: S3Storage) -> None:
    table = pa.table(
        {
            "date": ["2024-01-02"],
            "symbol": ["SPY"],
            "log_return": [0.01],
        }
    )
    storage.write_parquet(BUCKET, "year=2024/month=01/returns.parquet", table)
    result = storage.read_parquet(BUCKET, "year=2024/month=01/returns.parquet")
    assert result.to_pydict() == table.to_pydict()


def test_list_keys_filters_by_prefix(storage: S3Storage) -> None:
    storage.write_json(BUCKET, "a/1.json", {})
    storage.write_json(BUCKET, "a/2.json", {})
    storage.write_json(BUCKET, "b/3.json", {})
    assert storage.list_keys(BUCKET, "a/") == ["a/1.json", "a/2.json"]
