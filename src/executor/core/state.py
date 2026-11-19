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
from common.models import decimal_string
from executor.core.models import (
    ExecutionResult,
    IdempotencyClaim,
    ProposalRecord,
)

logger = get_logger(__name__)

STATE_PARTITION = "STATE"
CURRENT_SORT_KEY = "CURRENT"
PROPOSAL_PARTITION = "PROPOSAL"
IDEMPOTENCY_PARTITION = "IDEMPOTENCY"
IDEMPOTENCY_TTL = timedelta(hours=24)
UNRESOLVED_STATUS = "UNRESOLVED"
_WEIGHT_SUM_TOLERANCE = 1e-6


class ExecutionState(Protocol):
    """State contract for the rebalance execution flow."""

    def read_proposal(self, proposal_id: str) -> ProposalRecord | None: ...

    def transition_to_executing(
        self,
        proposal_id: str,
        allow_executing: bool = False,
    ) -> None: ...

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

    def claim_idempotency(
        self,
        idempotency_key: str,
        proposal_id: str,
        now: datetime,
        staleness_seconds: int | None = None,
    ) -> IdempotencyClaim: ...

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

    def transition_to_executing(self, proposal_id: str, allow_executing: bool = False) -> None:
        # An UNRESOLVED proposal may be resumed; anything else must be
        # PENDING. Only a retry that took over a stale EXECUTING claim (its
        # original owner is provably dead) may re-enter from EXECUTING.
        sources = [":pending", ":unresolved"] + ([":executing"] if allow_executing else [])
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": PROPOSAL_PARTITION}, "sk": {"S": proposal_id}},
                UpdateExpression="SET #status = :executing",
                ConditionExpression=f"#status IN ({', '.join(sources)})",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": {"S": "PENDING"},
                    ":unresolved": {"S": "UNRESOLVED"},
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
            values[":account_value"] = {"N": decimal_string(result.reconciliation.account_value)}
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
        # Weights are fractions of the account equity; uninvested cash is the
        # remainder, so the sum may be below one but never above it.
        if math.fsum(weights.values()) > 1.0 + _WEIGHT_SUM_TOLERANCE:
            raise StorageError("reconciled weights must not exceed the account equity")
        if not math.isfinite(account_value) or account_value <= 0.0:
            raise StorageError("account value must be finite and positive")
        reconciled_at = now.astimezone(UTC).isoformat(timespec="microseconds")
        item = {
            "pk": {"S": STATE_PARTITION},
            "sk": {"S": CURRENT_SORT_KEY},
            "weights": {
                "M": {symbol: {"N": decimal_string(weight)} for symbol, weight in weights.items()}
            },
            "account_value": {"N": decimal_string(account_value)},
            "reconciled_at": {"S": reconciled_at},
        }
        # Monotonic write (AGENTS.md section 7.2): an older reconciliation that
        # finishes late must never overwrite a newer one. ISO-8601 UTC strings
        # with fixed microseconds compare chronologically.
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(pk) OR reconciled_at < :reconciled_at",
                ExpressionAttributeValues={":reconciled_at": {"S": reconciled_at}},
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                logger.warning(
                    "stored reconciled state is newer; keeping it",
                    extra={"reconciled_at": reconciled_at},
                )
                return
            raise StorageError("failed to write reconciled state") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to write reconciled state") from exc

    def claim_idempotency(
        self,
        idempotency_key: str,
        proposal_id: str,
        now: datetime,
        staleness_seconds: int | None = None,
    ) -> IdempotencyClaim:
        """Claim the idempotency key or return the previously recorded claim.

        ``now`` comes from the injected clock so TTLs are deterministic in
        tests (AGENTS.md section 10). With ``staleness_seconds`` set, a claim
        left in EXECUTING by an invocation that is older than the bound may be
        taken over: its owner provably ended (bounded by the Lambda runtime)
        without recording a result, so the retry resumes it and verifies every
        planned order in the broker before sending anything.
        """
        created = now.astimezone(UTC)
        expires = created + IDEMPOTENCY_TTL
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    "pk": {"S": IDEMPOTENCY_PARTITION},
                    "sk": {"S": idempotency_key},
                    "proposal_id": {"S": proposal_id},
                    "status": {"S": "EXECUTING"},
                    # Fixed microseconds so the takeover cutoff compares
                    # chronologically as a plain string (AGENTS.md section 10).
                    "created_at": {"S": created.isoformat(timespec="microseconds")},
                    "expires_at": {"S": expires.isoformat(timespec="microseconds")},
                    "ttl": {"N": str(int(expires.timestamp()))},
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                existing = self._get_item(IDEMPOTENCY_PARTITION, idempotency_key)
                if existing is None:
                    raise StorageError("idempotency claim disappeared") from exc
                previous = _parse_claim(idempotency_key, existing)
                if previous.status == UNRESOLVED_STATUS and previous.proposal_id == proposal_id:
                    return self._reclaim_unresolved(
                        idempotency_key,
                        proposal_id,
                        created,
                        expires,
                        previous,
                    )
                if (
                    staleness_seconds is not None
                    and previous.status == "EXECUTING"
                    and previous.proposal_id == proposal_id
                    and self._claim_is_stale(previous, now, staleness_seconds)
                ):
                    return self._reclaim_stale_executing(
                        idempotency_key,
                        proposal_id,
                        created,
                        expires,
                        now,
                        staleness_seconds,
                    )
                return previous
            raise StorageError("failed to claim idempotency key") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to claim idempotency key") from exc
        return IdempotencyClaim(
            idempotency_key=idempotency_key,
            proposal_id=proposal_id,
            status="EXECUTING",
            result=None,
            fresh=True,
        )

    @staticmethod
    def _claim_is_stale(
        previous: IdempotencyClaim,
        now: datetime,
        staleness_seconds: int,
    ) -> bool:
        """Compare the claim's created_at with the staleness cutoff chronologically.

        Precondition: the stored created_at is parsed into a timezone-aware
        datetime; the conditional update performs the same comparison on the
        stored ISO-8601 strings with fixed microseconds.
        """
        if previous.created_at is None:
            return False
        cutoff = now.astimezone(UTC) - timedelta(seconds=staleness_seconds)
        return previous.created_at <= cutoff

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

    def _reclaim_stale_executing(
        self,
        idempotency_key: str,
        proposal_id: str,
        created: datetime,
        expires: datetime,
        now: datetime,
        staleness_seconds: int,
    ) -> IdempotencyClaim:
        """Take over an EXECUTING claim whose owner is provably dead, atomically.

        The conditional update requires the claim to still be EXECUTING and
        older than the staleness cutoff, so two concurrent retries cannot both
        take over, and a still-running original owner is excluded by the bound.
        The winner gets a fresh claim with no recorded result; the loser gets
        the in-flight claim and raises an IdempotencyConflict upstream.
        """
        cutoff = (now.astimezone(UTC) - timedelta(seconds=staleness_seconds)).isoformat(
            timespec="microseconds"
        )
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": IDEMPOTENCY_PARTITION}, "sk": {"S": idempotency_key}},
                UpdateExpression=(
                    "SET created_at = :created_at, expires_at = :expires_at, #ttl = :ttl"
                ),
                ConditionExpression="#status = :executing AND created_at <= :cutoff",
                ExpressionAttributeNames={"#status": "status", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":executing": {"S": "EXECUTING"},
                    ":cutoff": {"S": cutoff},
                    ":created_at": {"S": created.isoformat(timespec="microseconds")},
                    ":expires_at": {"S": expires.isoformat(timespec="microseconds")},
                    ":ttl": {"N": str(int(expires.timestamp()))},
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                existing = self._get_item(IDEMPOTENCY_PARTITION, idempotency_key)
                if existing is None:
                    raise StorageError("idempotency claim disappeared") from exc
                return _parse_claim(idempotency_key, existing)
            raise StorageError("failed to reclaim a stale idempotency key") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to reclaim a stale idempotency key") from exc
        return IdempotencyClaim(
            idempotency_key=idempotency_key,
            proposal_id=proposal_id,
            status="EXECUTING",
            created_at=created,
            result=None,
            fresh=True,
            stale_takeover=True,
        )

    def _reclaim_unresolved(
        self,
        idempotency_key: str,
        proposal_id: str,
        created: datetime,
        expires: datetime,
        previous: IdempotencyClaim,
    ) -> IdempotencyClaim:
        """Take over an UNRESOLVED claim for a resumed attempt, atomically.

        Postconditions: the first retry to win the conditional update gets a
        fresh claim that still carries the previous orders, so it can resume;
        a concurrent retry that loses gets the non-fresh in-flight claim.
        """
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": {"S": IDEMPOTENCY_PARTITION}, "sk": {"S": idempotency_key}},
                UpdateExpression=(
                    "SET #status = :executing, created_at = :created_at, "
                    "expires_at = :expires_at, #ttl = :ttl"
                ),
                ConditionExpression="#status = :unresolved",
                ExpressionAttributeNames={"#status": "status", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":executing": {"S": "EXECUTING"},
                    ":unresolved": {"S": UNRESOLVED_STATUS},
                    ":created_at": {"S": created.isoformat()},
                    ":expires_at": {"S": expires.isoformat()},
                    ":ttl": {"N": str(int(expires.timestamp()))},
                },
            )
        except ClientError as exc:
            if _is_conditional_failure(exc):
                existing = self._get_item(IDEMPOTENCY_PARTITION, idempotency_key)
                if existing is None:
                    raise StorageError("idempotency claim disappeared") from exc
                return _parse_claim(idempotency_key, existing)
            raise StorageError("failed to reclaim idempotency key") from exc
        except BotoCoreError as exc:
            raise StorageError("failed to reclaim idempotency key") from exc
        return IdempotencyClaim(
            idempotency_key=idempotency_key,
            proposal_id=proposal_id,
            status="EXECUTING",
            result=previous.result,
            fresh=True,
        )

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
        created_at = datetime.fromisoformat(item["created_at"]["S"])
        expires_at = datetime.fromisoformat(item["expires_at"]["S"])
        run_date_raw = item["run_date"]["S"]
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError("proposal item is missing required attributes") from exc
    w_star = item.get("w_star")
    weights_raw = w_star.get("M") if isinstance(w_star, dict) else None
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
    created_at: datetime | None = None
    created_raw = item.get("created_at", {}).get("S")
    if created_raw:
        try:
            created_at = datetime.fromisoformat(created_raw)
        except ValueError as exc:
            raise StorageError("stored claim created_at is invalid") from exc
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
        created_at=created_at,
        result=result,
        fresh=False,
    )
