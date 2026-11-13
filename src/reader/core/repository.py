from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import NotFoundError, StorageError
from common.storage import ObjectStorage
from reader.core.models import CurrentState

LATEST_KEY = "latest.json"
_PROPOSAL_KEY_TEMPLATE = "run_date={run_date}/proposal.json"


class GoldRepository(Protocol):
    """Loads the raw proposal documents stored in the gold layer."""

    def load_proposal(self, run_date: date | None) -> dict[str, Any]: ...


class StateReader(Protocol):
    """Reads the reconciled portfolio state, if it exists."""

    def read_current_state(self) -> CurrentState | None: ...


class S3GoldRepository:
    """Gold layer adapter backed by S3 through the shared object storage."""

    def __init__(self, storage: ObjectStorage, bucket: str) -> None:
        self._storage = storage
        self._bucket = bucket

    def load_proposal(self, run_date: date | None) -> dict[str, Any]:
        key = _proposal_key(run_date)
        if not self._storage.exists(self._bucket, key):
            raise NotFoundError(f"proposal not found for {key}")
        try:
            return self._storage.read_json(self._bucket, key)
        except StorageError as exc:
            raise StorageError(f"failed to read proposal {key}") from exc


class DynamoCurrentState:
    """DynamoDB adapter for the STATE/CURRENT item."""

    def __init__(
        self,
        table_name: str,
        client: Any = None,
        region_name: str | None = None,
    ) -> None:
        self._table_name = table_name
        self._client: Any = (
            client if client is not None else boto3.client("dynamodb", region_name=region_name)
        )

    def read_current_state(self) -> CurrentState | None:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to read portfolio state") from exc
        item = response.get("Item")
        if item is None:
            return None
        return _parse_state(item)


def _proposal_key(run_date: date | None) -> str:
    if run_date is None:
        return LATEST_KEY
    return _PROPOSAL_KEY_TEMPLATE.format(run_date=run_date.isoformat())


def _parse_state(item: dict[str, Any]) -> CurrentState:
    raw = item.get("weights", {}).get("M")
    if not isinstance(raw, dict) or not raw:
        raise StorageError("state weights must be a non-empty map")
    weights: dict[str, float] = {}
    for symbol, encoded in raw.items():
        if not isinstance(encoded, dict) or "N" not in encoded:
            raise StorageError("state weights must be numeric")
        weights[str(symbol)] = float(str(encoded["N"]))
    reconciled_at = item.get("reconciled_at", {}).get("S")
    account_value = item.get("account_value", {}).get("N")
    return CurrentState(
        weights=weights,
        reconciled_at=reconciled_at,
        account_value=float(str(account_value)) if account_value is not None else None,
    )
