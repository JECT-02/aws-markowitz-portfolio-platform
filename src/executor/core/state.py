from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import StorageError
from common.logging import get_logger
from executor.core.models import (
    ExecutionResult,
    IdempotencyClaim,
    ProposalRecord,
    decimal_string,
)

logger = get_logger(__name__)

STATE_PARTITION = "STATE"
CURRENT_SORT_KEY = "CURRENT"
PROPOSAL_PARTITION = "PROPOSAL"
IDEMPOTENCY_PARTITION = "IDEMPOTENCY"
IDEMPOTENCY_TTL = timedelta(hours=24)
_WEIGHT_SUM_TOLERANCE = 1e-6


class ExecutionState(Protocol):
    """State contract for the rebalance execution flow."""

    def read_proposal(self, proposal_id: str) -> ProposalRecord | None: ...

    def transition_to_executing(self, proposal_id: str) -> None: ...

    def write_execution_result(
        self,
        proposal_id: str,
        result: ExecutionResult,
        now: datetime,
    ) -> None: ...

    def write_reconciled_state(
        self,
        weights: dict[str, float],
        account_value: float,
        now: datetime,
    ) -> None: ...

    def claim_idempotency(self, idempotency_key: str, proposal_id: str) -> IdempotencyClaim: ...

    def write_idempotency_result(
        self,
        idempotency_key: str,
        result: ExecutionResult,
        now: datetime,
    ) -> None: ...


class DynamoExecutionState:
    """DynamoDB implementation of the execution state contract.

    All writes are conditional or idempotent: the proposal claim requires the
    PENDING status, the idempotency claim requires an absent key and every
    result write only refines an already claimed key.
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

    def read_proposal(self, proposal_id: str) -> ProposalRecord | None:
        item = self._get_item(PROPOSAL_PARTITION, proposal_id)
        if item is None:
            return None
        return _parse_proposal(proposal_id, item)

    def transition_to_executing(self, proposal_id: str) -> None:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": PROPOSAL_PARTITION}, "sk": {"S": proposal_id}},
                UpdateExpression="SET #status = :executing",
                ConditionExpression="#status = :pending",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": {"S": "PENDING"},
                    ":executing": {"S": "EXECUTING"},
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                logger.warning("proposal not PENDING", extra={"proposal_id": proposal_id})
                raise StorageError(f"proposal {proposal_id} is not PENDING") from exc
            raise StorageError("failed to transition proposal to EXECUTING") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to transition proposal to EXECUTING") from exc

    def write_execution_result(
        self,
        proposal_id: str,
        result: ExecutionResult,
        now: datetime,
    ) -> None:
        expression = "SET #status = :status, executed_at = :executed_at"
        values: dict[str, dict[str, str]] = {
            ":status": {"S": result.status.value},
            ":executed_at": {"S": now.astimezone(UTC).isoformat()},
        }
        names = {"#status": "status"}
        if result.error is not None:
            expression = f"{expression}, #error = :error"
            names["#error"] = "error"
            values[":error"] = {"S": result.error[:900]}
        elif result.reconciliation is not None:
            expression = f"{expression}, account_value = :account_value"
            values[":account_value"] = {
                "N": decimal_string(result.reconciliation.account_value)
            }
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": PROPOSAL_PARTITION}, "sk": {"S": proposal_id}},
                UpdateExpression=expression,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to write execution result") from exc

    def write_reconciled_state(
        self,
        weights: dict[str, float],
        account_value: float,
        now: datetime,
    ) -> None:
        if not weights:
            raise StorageError("reconciled weights must not be empty")
        if any(weight < 0.0 or not math.isfinite(weight) for weight in weights.values()):
            raise StorageError("reconciled weights must be finite and non-negative")
        if abs(math.fsum(weights.values()) - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise StorageError("reconciled weights must sum to one")
        if not math.isfinite(account_value) or account_value <= 0.0:
            raise StorageError("account value must be finite and positive")
        item = {
            "pk": {"S": STATE_PARTITION},
            "sk": {"S": CURRENT_SORT_KEY},
            "weights": {
                "M": {symbol: {"N": decimal_string(weight)} for symbol, weight in weights.items()}
            },
            "account_value": {"N": decimal_string(account_value)},
            "reconciled_at": {"S": now.astimezone(UTC).isoformat()},
        }
        try:
            self._client.put_item(TableName=self._table_name, Item=item)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to write reconciled state") from exc

    def claim_idempotency(self, idempotency_key: str, proposal_id: str) -> IdempotencyClaim:
        """Claim the idempotency key or return the previously recorded claim."""
        created = datetime.now(UTC)
        expires = created + IDEMPOTENCY_TTL
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    "pk": {"S": IDEMPOTENCY_PARTITION},
                    "sk": {"S": idempotency_key},
                    "proposal_id": {"S": proposal_id},
                    "status": {"S": "EXECUTING"},
                    "created_at": {"S": created.isoformat()},
                    "expires_at": {"S": expires.isoformat()},
                    "ttl": {"N": str(int(expires.timestamp()))},
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                existing = self._get_item(IDEMPOTENCY_PARTITION, idempotency_key)
                if existing is None:
                    raise StorageError("idempotency claim disappeared") from exc
                return _parse_claim(idempotency_key, existing)
            raise StorageError("failed to claim idempotency key") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to claim idempotency key") from exc
        return IdempotencyClaim(
            idempotency_key=idempotency_key,
            proposal_id=proposal_id,
            status="EXECUTING",
            result=None,
        )

    def write_idempotency_result(
        self,
        idempotency_key: str,
        result: ExecutionResult,
        now: datetime,
    ) -> None:
        expires = now.astimezone(UTC) + IDEMPOTENCY_TTL
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": IDEMPOTENCY_PARTITION}, "sk": {"S": idempotency_key}},
                UpdateExpression=(
                    "SET #status = :status, #result = :result, "
                    "#ttl = :ttl, expires_at = :expires_at"
                ),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#status": "status", "#result": "result", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":status": {"S": result.status.value},
                    ":result": {"S": json.dumps(result.model_dump(mode="json"))},
                    ":expires_at": {"S": expires.isoformat()},
                    ":ttl": {"N": str(int(expires.timestamp()))},
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                raise StorageError(f"idempotency key {idempotency_key} was never claimed") from exc
            raise StorageError("failed to write idempotency result") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to write idempotency result") from exc

    def _get_item(self, partition: str, sort_key: str) -> dict[str, Any] | None:
        try:
            response = self._client.get_item(
                TableName=self._table_name,
                Key={"pk": {"S": partition}, "sk": {"S": sort_key}},
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError("failed to read execution state") from exc
        item = response.get("Item")
        return item if isinstance(item, dict) else None


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _parse_proposal(proposal_id: str, item: dict[str, Any]) -> ProposalRecord:
    try:
        status = item["status"]["S"]
        weights_raw = item["w_star"]["M"]
        created_at = datetime.fromisoformat(item["created_at"]["S"])
        expires_at = datetime.fromisoformat(item["expires_at"]["S"])
        run_date_raw = item["run_date"]["S"]
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError("proposal item is missing required attributes") from exc
    return ProposalRecord(
        proposal_id=proposal_id,
        status=status,
        weights=_parse_number_map(weights_raw, "proposal weights"),
        prices={},
        created_at=created_at,
        expires_at=expires_at,
        run_date=date.fromisoformat(run_date_raw),
    )


def _parse_number_map(raw: Any, description: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise StorageError(f"{description} must be a numeric map")
    values: dict[str, float] = {}
    for symbol, encoded in raw.items():
        if not isinstance(encoded, dict) or "N" not in encoded:
            raise StorageError(f"{description} must be numeric")
        values[str(symbol)] = float(str(encoded["N"]))
    return values


def _parse_claim(idempotency_key: str, item: dict[str, Any]) -> IdempotencyClaim:
    proposal_id = str(item.get("proposal_id", {}).get("S", ""))
    status = str(item.get("status", {}).get("S", "EXECUTING"))
    result: ExecutionResult | None = None
    result_raw = item.get("result", {}).get("S")
    if result_raw:
        try:
            result = ExecutionResult.model_validate(json.loads(result_raw))
        except (ValueError, TypeError) as exc:
            raise StorageError("stored idempotency result is invalid") from exc
    return IdempotencyClaim(
        idempotency_key=idempotency_key,
        proposal_id=proposal_id,
        status=status,
        result=result,
    )
