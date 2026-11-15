"""DynamoDB persistence for the fast monitoring loop.

Each run writes one ``MONITOR`` item keyed by its UTC timestamp; records
expire through the DynamoDB TTL attribute so the table stays bounded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import StorageError
from common.logging import get_logger
from common.models import decimal_string
from monitor.core.drift import DriftSnapshot

logger = get_logger(__name__)

MONITOR_PARTITION = "MONITOR"
MONITOR_TTL_DAYS = 7


class MonitorState(Protocol):
    """Persistence contract for fast-loop snapshots."""

    def write_snapshot(self, snapshot: DriftSnapshot, now: datetime) -> None: ...


class DynamoMonitorState:
    """DynamoDB implementation of the monitor state contract.

    Writes are idempotent per timestamp: the sort key is the UTC observation
    time, so a repeated event for the same timestamp overwrites the same
    item instead of accumulating duplicates.
    """

    def __init__(
        self,
        table_name: str,
        client: BaseClient | None = None,
        region_name: str | None = None,
    ) -> None:
        self._table_name = table_name
        self._client: Any = (
            client if client is not None else boto3.client("dynamodb", region_name=region_name)
        )

    def write_snapshot(self, snapshot: DriftSnapshot, now: datetime) -> None:
        """Store one validated snapshot with a bounded TTL.

        Preconditions: ``snapshot`` is fully validated; ``now`` is
        timezone-aware. Postconditions: the item carries the weights, the
        drift, the proposal reference and an epoch-seconds TTL attribute;
        storage failures propagate as ``StorageError``.
        """
        timestamp = now.astimezone(UTC)
        expires = timestamp + timedelta(days=MONITOR_TTL_DAYS)
        item = {
            "pk": {"S": MONITOR_PARTITION},
            "sk": {"S": timestamp.isoformat()},
            "weights_live": {
                "M": {
                    symbol: {"N": decimal_string(weight)}
                    for symbol, weight in sorted(snapshot.weights_live.items())
                }
            },
            "drift_l1": {"N": decimal_string(snapshot.drift_l1)},
            "rebalance_required": {"BOOL": snapshot.rebalance_required},
            "proposal_id": {"S": snapshot.proposal_id},
            "run_date": {"S": snapshot.run_date.isoformat()},
            "computed_at": {"S": timestamp.isoformat()},
            "ttl": {"N": str(int(expires.timestamp()))},
        }
        try:
            self._client.put_item(TableName=self._table_name, Item=item)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to write the monitor snapshot") from exc
        logger.info(
            "monitor snapshot written",
            extra={
                "drift_l1": snapshot.drift_l1,
                "proposal_id": snapshot.proposal_id,
            },
        )
