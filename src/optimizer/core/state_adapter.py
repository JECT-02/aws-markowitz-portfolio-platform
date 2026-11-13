from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import DataValidationError, StorageError
from common.logging import get_logger
from common.models import RunMetadata
from optimizer.core.models import Proposal

logger = get_logger(__name__)

STATE_PARTITION = "STATE"
CURRENT_SORT_KEY = "CURRENT"
PROPOSAL_PARTITION = "PROPOSAL"
PROPOSAL_TTL = timedelta(hours=24)
WEIGHT_SUM_TOLERANCE = 1e-6
_UNKNOWN_ERROR = "UnknownError"


class PortfolioState(Protocol):
    """Persists proposals and resolves the current portfolio weights."""

    def read_actual_weights(self) -> dict[str, float] | None: ...

    def write_proposal(self, proposal: Proposal) -> None: ...

    def write_failure(self, proposal_id: str, metadata: RunMetadata, reason: str) -> None: ...


class DynamoPortfolioState:
    """DynamoDB implementation of the portfolio state contract."""

    def __init__(
        self,
        table_name: str,
        client: BaseClient | None = None,
        region_name: str | None = None,
    ) -> None:
        self._table_name = table_name
        self._client: Any = (
            client
            if client is not None
            else boto3.client("dynamodb", region_name=region_name)
        )

    def read_actual_weights(self) -> dict[str, float] | None:
        response = self._get_item(STATE_PARTITION, CURRENT_SORT_KEY)
        if response is None:
            logger.warning("state item not found, actual weights remain undefined")
            return None
        attributes = cast(Mapping[str, Any], response.get("weights", {}))
        raw = attributes.get("M")
        if not isinstance(raw, Mapping):
            raise DataValidationError("state weights must be a map")
        weights: dict[str, float] = {}
        for symbol, encoded in raw.items():
            if not isinstance(encoded, Mapping) or "N" not in encoded:
                raise DataValidationError("state weights must be numeric")
            weights[str(symbol)] = float(str(encoded["N"]))
        _validate_weights(weights)
        return weights

    def write_proposal(self, proposal: Proposal) -> None:
        created_at = proposal.created_at.astimezone(UTC)
        item = {
            "pk": {"S": PROPOSAL_PARTITION},
            "sk": {"S": proposal.proposal_id},
            "w_star": {
                "M": {symbol: _number(weight) for symbol, weight in proposal.weights.items()}
            },
            "status": {"S": proposal.status},
            "created_at": {"S": created_at.isoformat()},
            "expires_at": {"S": (created_at + PROPOSAL_TTL).isoformat()},
            "ttl": {"N": str(int((created_at + PROPOSAL_TTL).timestamp()))},
            "run_date": {"S": proposal.run_date.isoformat()},
            "run_id": {"S": proposal.metadata.run_id},
            "code_version": {"S": proposal.metadata.code_version},
            "drift_l1": _number(proposal.drift_l1),
            "rebalance_required": {"BOOL": proposal.rebalance_required},
        }
        self._put_if_absent(item, proposal.proposal_id)

    def write_failure(self, proposal_id: str, metadata: RunMetadata, reason: str) -> None:
        created_at = metadata.fetched_at.astimezone(UTC)
        item = {
            "pk": {"S": PROPOSAL_PARTITION},
            "sk": {"S": proposal_id},
            "status": {"S": "FAILED"},
            "error": {"S": reason[:900]},
            "created_at": {"S": created_at.isoformat()},
            "expires_at": {"S": (created_at + PROPOSAL_TTL).isoformat()},
            "ttl": {"N": str(int((created_at + PROPOSAL_TTL).timestamp()))},
            "run_date": {"S": metadata.run_date.isoformat()},
            "run_id": {"S": metadata.run_id},
            "code_version": {"S": metadata.code_version},
        }
        self._put_if_absent(item, proposal_id)

    def _get_item(self, partition: str, sort_key: str) -> Mapping[str, Any] | None:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={"pk": {"S": partition}, "sk": {"S": sort_key}},
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to read portfolio state") from exc
        item = response.get("Item")
        if item is None:
            return None
        return cast(Mapping[str, Any], item)

    def _put_if_absent(self, item: dict[str, Any], proposal_id: str) -> None:
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                logger.warning(
                    "proposal already persisted",
                    extra={"proposal_id": proposal_id},
                )
                return
            raise StorageError("failed to write portfolio state") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to write portfolio state") from exc


def _number(value: float) -> dict[str, str]:
    if not math.isfinite(value):
        raise DataValidationError("state values must be finite")
    return {"N": str(Decimal(str(value)))}


def _validate_weights(weights: Mapping[str, float]) -> None:
    if not weights:
        raise DataValidationError("state weights must not be empty")
    if any(not math.isfinite(weight) or weight < 0.0 for weight in weights.values()):
        raise DataValidationError("state weights must be finite and non-negative")
    if abs(math.fsum(weights.values()) - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise DataValidationError("state weights must sum to one")


def proposal_identifier(metadata: RunMetadata) -> str:
    """Return the deterministic proposal identifier for a pipeline run."""
    return f"{metadata.run_date.isoformat()}-{metadata.run_id}"
