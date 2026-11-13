from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from common.errors import StorageError
from executor.core.models import (
    ExecutionResult,
    ProposalStatus,
    Reconciliation,
)
from executor.core.state import DynamoExecutionState

TABLE_NAME = "portfolio-state"
NOW = datetime(2026, 11, 13, 21, 0, 0, tzinfo=UTC)
PROPOSAL_ID = "2026-11-13-run-abc"


def _put_proposal(client: Any, status: str = "PENDING") -> None:
    created = NOW - timedelta(minutes=30)
    client.put_item(
        TableName=TABLE_NAME,
        Item={
            "pk": {"S": "PROPOSAL"},
            "sk": {"S": PROPOSAL_ID},
            "status": {"S": status},
            "w_star": {"M": {"SPY": {"N": "0.6"}, "QQQ": {"N": "0.4"}}},
            "created_at": {"S": created.isoformat()},
            "expires_at": {"S": (created + timedelta(hours=24)).isoformat()},
            "run_date": {"S": "2026-11-13"},
        },
    )


def _result(
    status: ProposalStatus = ProposalStatus.EXECUTED,
    reconciliation: Reconciliation | None = None,
    error: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        proposal_id=PROPOSAL_ID,
        status=status,
        orders=[],
        reconciliation=reconciliation,
        error=error,
    )


@pytest.fixture
def state_table():  # type: ignore[no-untyped-def]
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
        )
        yield client


def _state() -> DynamoExecutionState:
    return DynamoExecutionState(TABLE_NAME, region_name="us-east-1")


class TestReadProposal:
    def test_returns_none_for_unknown_proposal(self, state_table) -> None:
        assert _state().read_proposal("missing") is None

    def test_parses_the_stored_proposal(self, state_table) -> None:
        _put_proposal(state_table)
        record = _state().read_proposal(PROPOSAL_ID)
        assert record is not None
        assert record.status == "PENDING"
        assert record.weights == {"SPY": 0.6, "QQQ": 0.4}
        assert record.run_date == date(2026, 11, 13)
        assert record.expires_at > record.created_at

    def test_missing_attributes_raise_storage_error(self, state_table) -> None:
        state_table.put_item(
            TableName=TABLE_NAME,
            Item={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )
        with pytest.raises(StorageError, match="missing required attributes"):
            _state().read_proposal(PROPOSAL_ID)


class TestTransitionToExecuting:
    def test_pending_proposal_transitions(self, state_table) -> None:
        _put_proposal(state_table)
        _state().transition_to_executing(PROPOSAL_ID)
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert item["status"]["S"] == "EXECUTING"

    def test_non_pending_proposal_is_rejected(self, state_table) -> None:
        _put_proposal(state_table, status="NO_REBALANCE")
        with pytest.raises(StorageError, match="not PENDING"):
            _state().transition_to_executing(PROPOSAL_ID)
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert item["status"]["S"] == "NO_REBALANCE"


class TestWriteExecutionResult:
    def test_executed_result_stores_status_and_account_value(self, state_table) -> None:
        _put_proposal(state_table)
        result = _result(
            reconciliation=Reconciliation(
                weights={"SPY": 0.6, "QQQ": 0.4},
                account_value=10_000.0,
                reconciled_at=NOW,
            )
        )
        _state().write_execution_result(PROPOSAL_ID, result, NOW)
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert item["status"]["S"] == "EXECUTED"
        assert item["account_value"]["N"] == "10000.0"
        assert item["executed_at"]["S"] == NOW.isoformat()

    def test_failed_result_stores_the_error(self, state_table) -> None:
        _put_proposal(state_table)
        _state().write_execution_result(
            PROPOSAL_ID,
            _result(status=ProposalStatus.FAILED, error="broker rejected the order"),
            NOW,
        )
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert item["status"]["S"] == "FAILED"
        assert item["error"]["S"] == "broker rejected the order"


class TestWriteReconciledState:
    def test_writes_current_state(self, state_table) -> None:
        _state().write_reconciled_state({"SPY": 0.6, "QQQ": 0.4}, 10_000.0, NOW)
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        assert item["weights"]["M"]["SPY"]["N"] == "0.6"
        assert item["account_value"]["N"] == "10000.0"
        assert item["reconciled_at"]["S"] == NOW.isoformat()

    def test_rejects_weights_not_summing_to_one(self, state_table) -> None:
        with pytest.raises(StorageError, match="sum to one"):
            _state().write_reconciled_state({"SPY": 0.5, "QQQ": 0.4}, 10_000.0, NOW)

    def test_rejects_negative_weights(self, state_table) -> None:
        with pytest.raises(StorageError, match="non-negative"):
            _state().write_reconciled_state({"SPY": 1.5, "QQQ": -0.5}, 10_000.0, NOW)


class TestIdempotency:
    def test_first_claim_is_fresh(self, state_table) -> None:
        claim = _state().claim_idempotency("key-1", PROPOSAL_ID)
        assert claim.result is None
        assert claim.status == "EXECUTING"

    def test_repeated_claim_returns_the_recorded_one(self, state_table) -> None:
        state = _state()
        state.claim_idempotency("key-1", PROPOSAL_ID)
        repeat = state.claim_idempotency("key-1", PROPOSAL_ID)
        assert repeat.proposal_id == PROPOSAL_ID
        assert repeat.result is None

    def test_claim_survives_a_different_proposal_id(self, state_table) -> None:
        state = _state()
        state.claim_idempotency("key-1", PROPOSAL_ID)
        repeat = state.claim_idempotency("key-1", "other-proposal")
        assert repeat.proposal_id == PROPOSAL_ID

    def test_result_write_updates_the_claim(self, state_table) -> None:
        state = _state()
        state.claim_idempotency("key-1", PROPOSAL_ID)
        result = _result(
            reconciliation=Reconciliation(
                weights={"SPY": 1.0},
                account_value=1_000.0,
                reconciled_at=NOW,
            )
        )
        state.write_idempotency_result("key-1", result, NOW)
        repeat = state.claim_idempotency("key-1", PROPOSAL_ID)
        assert repeat.result is not None
        assert repeat.result.status is ProposalStatus.EXECUTED
        assert repeat.result.reconciliation is not None
        assert repeat.result.reconciliation.account_value == pytest.approx(1_000.0)

    def test_result_write_without_claim_is_rejected(self, state_table) -> None:
        with pytest.raises(StorageError, match="never claimed"):
            _state().write_idempotency_result("key-1", _result(), NOW)
