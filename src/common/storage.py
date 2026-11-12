from __future__ import annotations

import io
import json
from typing import Any, Protocol

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.exceptions import BotoCoreError, ClientError
from mypy_boto3_s3.client import S3Client

from common.errors import StorageError

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class ObjectStorage(Protocol):
    """Minimal object storage interface used by the pipeline."""

    def exists(self, bucket: str, key: str) -> bool: ...

    def write_json_if_absent(self, bucket: str, key: str, payload: dict[str, Any]) -> bool: ...

    def write_json(self, bucket: str, key: str, payload: dict[str, Any]) -> None: ...

    def read_json(self, bucket: str, key: str) -> dict[str, Any]: ...

    def write_parquet(self, bucket: str, key: str, table: pa.Table) -> None: ...

    def read_parquet(self, bucket: str, key: str) -> pa.Table: ...

    def list_keys(self, bucket: str, prefix: str) -> list[str]: ...


class S3Storage:
    """Object storage backed by Amazon S3."""

    def __init__(self, client: S3Client | None = None) -> None:
        self._client: S3Client = client if client is not None else boto3.client("s3")

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise StorageError(f"failed to check object {bucket}/{key}") from exc
        return True

    def write_json_if_absent(self, bucket: str, key: str, payload: dict[str, Any]) -> bool:
        if self.exists(bucket, key):
            return False
        self.write_json(bucket, key, payload)
        return True

    def write_json(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode(
            "utf-8"
        )
        self._put(bucket, key, body, "application/json")

    def read_json(self, bucket: str, key: str) -> dict[str, Any]:
        raw = self._get(bucket, key)
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StorageError(f"invalid json stored at {bucket}/{key}") from exc
        if not isinstance(document, dict):
            raise StorageError(f"json object at {bucket}/{key} is not a mapping")
        return {str(name): value for name, value in document.items()}

    def write_parquet(self, bucket: str, key: str, table: pa.Table) -> None:
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression="snappy")
        self._put(bucket, key, buffer.getvalue(), "application/vnd.apache.parquet")

    def read_parquet(self, bucket: str, key: str) -> pa.Table:
        return pq.read_table(io.BytesIO(self._get(bucket, key)))

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    if key is not None:
                        keys.append(key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"failed to list objects under {bucket}/{prefix}") from exc
        return sorted(keys)

    def _get(self, bucket: str, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"failed to read object {bucket}/{key}") from exc

    def _put(self, bucket: str, key: str, body: bytes, content_type: str) -> None:
        try:
            self._client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"failed to write object {bucket}/{key}") from exc


def _is_not_found(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return error.get("Code") in _NOT_FOUND_CODES
