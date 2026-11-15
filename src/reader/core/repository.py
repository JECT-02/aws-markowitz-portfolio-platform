from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import NotFoundError, StorageError
from common.storage import ObjectStorage
from reader.core.models import CurrentState, MonitorRecord

LATEST_KEY = "latest.json"
_BACKTEST_LATEST_KEY = "backtest_latest.json"
_PROPOSAL_KEY_TEMPLATE = "run_date={run_date}/proposal.json"
_MONITOR_PARTITION = "MONITOR"


class GoldRepository(Protocol):
    """Loads the raw proposal documents stored in the gold layer."""

    def load_proposal(self, run_date: date | None) -> dict[str, Any]: ...

    def load_backtest(self) -> dict[str, Any]: ...


class StateReader(Protocol):
    """Reads the reconciled portfolio state, if it exists."""

    def read_current_state(self) -> CurrentState | None: ...


class MonitorHistory(Protocol):
    """Reads the latest fast-loop observation, if one exists."""

    def read_latest_monitor(self) -> MonitorRecord | None: ...


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

    def load_backtest(self) -> dict[str, Any]:
        """Load the weekly backtest report pointer artifact."""
        key = _BACKTEST_LATEST_KEY
        if not self._storage.exists(self._bucket, key):
            raise NotFoundError(f"backtest report not found for {key}")
        try:
            return self._storage.read_json(self._bucket, key)
        except StorageError as exc:
            raise StorageError(f"failed to read backtest report {key}") from exc


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


class DynamoMonitorHistory:
    """DynamoDB adapter for the newest MONITOR item."""

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

    def read_latest_monitor(self) -> MonitorRecord | None:
        """Return the newest fast-loop observation, or ``None`` before the
        first run. The sort key is the UTC ISO timestamp, so a descending
        query with ``Limit=1`` yields the freshest record.
        """
        try:
            response = self._client.query(
                TableName=self._table_name,
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": {"S": _MONITOR_PARTITION}},
                ScanIndexForward=False,
                Limit=1,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to read the monitor history") from exc
        items = response.get("Items", [])
        if not items:
            return None
        return _parse_monitor(items[0])


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


def _parse_monitor(item: dict[str, Any]) -> MonitorRecord:
    raw = item.get("weights_live", {}).get("M")
    if not isinstance(raw, dict):
        raise StorageError("monitor weights must be a map")
    weights: dict[str, float] = {}
    for symbol, encoded in raw.items():
        if not isinstance(encoded, dict) or "N" not in encoded:
            raise StorageError("monitor weights must be numeric")
        weights[str(symbol)] = float(str(encoded["N"]))
    drift_raw = item.get("drift_l1", {}).get("N")
    proposal_id = item.get("proposal_id", {}).get("S")
    computed_at = item.get("computed_at", {}).get("S")
    rebalance_raw = item.get("rebalance_required", {}).get("BOOL")
    if drift_raw is None or proposal_id is None or computed_at is None or rebalance_raw is None:
        raise StorageError("monitor record is missing required attributes")
    return MonitorRecord(
        weights_live=weights,
        drift_l1=float(str(drift_raw)),
        rebalance_required=bool(rebalance_raw),
        proposal_id=str(proposal_id),
        computed_at=str(computed_at),
    )
