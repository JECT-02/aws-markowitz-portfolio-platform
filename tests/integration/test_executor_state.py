from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import BotoCoreError, ClientError
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
KEY = "idem-key-1"


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
        assert float(item["account_value"]["N"]) == pytest.approx(10000.0)
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
        assert float(item["weights"]["M"]["SPY"]["N"]) == pytest.approx(0.6)
        assert item["account_value"]["N"] == "10000.000000000000"
        assert item["reconciled_at"]["S"] == NOW.isoformat(timespec="microseconds")

    def test_an_older_reconciliation_never_overwrites_a_newer_one(self, state_table) -> None:
        """AGENTS.md section 7.2: a late, older write must not roll the state back."""
        newer = NOW + timedelta(minutes=5)
        _state().write_reconciled_state({"SPY": 0.6}, 10_000.0, newer)
        _state().write_reconciled_state({"SPY": 0.1}, 10_000.0, NOW)
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        assert float(item["weights"]["M"]["SPY"]["N"]) == pytest.approx(0.6)
        assert item["reconciled_at"]["S"] == newer.isoformat(timespec="microseconds")

    def test_rejects_weights_above_the_account_equity(self, state_table) -> None:
        with pytest.raises(StorageError, match="account equity"):
            _state().write_reconciled_state({"SPY": 0.6, "QQQ": 0.5}, 10_000.0, NOW)

    def test_accepts_weights_below_one_with_uninvested_cash(self, state_table) -> None:
        _state().write_reconciled_state({"SPY": 0.5, "QQQ": 0.4}, 10_000.0, NOW)
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        assert float(item["weights"]["M"]["SPY"]["N"]) == pytest.approx(0.5)

    def test_rejects_negative_weights(self, state_table) -> None:
        with pytest.raises(StorageError, match="non-negative"):
            _state().write_reconciled_state({"SPY": 1.5, "QQQ": -0.5}, 10_000.0, NOW)


class TestIdempotency:
    def test_first_claim_is_fresh(self, state_table) -> None:
        claim = _state().claim_idempotency("key-1", PROPOSAL_ID, NOW)
        assert claim.result is None
        assert claim.status == "EXECUTING"

    def test_repeated_claim_returns_the_recorded_one(self, state_table) -> None:
        state = _state()
        state.claim_idempotency("key-1", PROPOSAL_ID, NOW)
        repeat = state.claim_idempotency("key-1", PROPOSAL_ID, NOW)
        assert repeat.proposal_id == PROPOSAL_ID
        assert repeat.result is None

    def test_claim_survives_a_different_proposal_id(self, state_table) -> None:
        state = _state()
        state.claim_idempotency("key-1", PROPOSAL_ID, NOW)
        repeat = state.claim_idempotency("key-1", "other-proposal", NOW)
        assert repeat.proposal_id == PROPOSAL_ID

    def test_result_write_updates_the_claim(self, state_table) -> None:
        state = _state()
        state.claim_idempotency("key-1", PROPOSAL_ID, NOW)
        result = _result(
            reconciliation=Reconciliation(
                weights={"SPY": 1.0},
                account_value=1_000.0,
                reconciled_at=NOW,
            )
        )
        state.write_idempotency_result("key-1", result, NOW)
        repeat = state.claim_idempotency("key-1", PROPOSAL_ID, NOW)
        assert repeat.result is not None
        assert repeat.result.status is ProposalStatus.EXECUTED
        assert repeat.result.reconciliation is not None
        assert repeat.result.reconciliation.account_value == pytest.approx(1_000.0)

    def test_result_write_without_claim_is_rejected(self, state_table) -> None:
        with pytest.raises(StorageError, match="never claimed"):
            _state().write_idempotency_result("key-1", _result(), NOW)


class _FailingClient:
    """DynamoDB client stub that raises the configured error once."""

    def __init__(self, error: Exception, method: str) -> None:
        self._error = error
        self._method = method
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            if name == self._method:
                raise self._error
            raise AssertionError(f"unexpected call {name}")

        return _call


def _client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "operation")


class TestStorageFailures:
    def test_transition_with_generic_client_error(self) -> None:
        client = _FailingClient(_client_error("InternalServerError", "boom"), "update_item")
        with pytest.raises(StorageError, match="transition"):
            DynamoExecutionState(TABLE_NAME, client=client).transition_to_executing("p")

    def test_transition_with_botocore_error(self) -> None:
        client = _FailingClient(BotoCoreError(), "update_item")
        with pytest.raises(StorageError, match="transition"):
            DynamoExecutionState(TABLE_NAME, client=client).transition_to_executing("p")

    def test_result_write_failure(self) -> None:
        client = _FailingClient(_client_error("InternalServerError", "boom"), "update_item")
        with pytest.raises(StorageError, match="execution result"):
            DynamoExecutionState(TABLE_NAME, client=client).write_execution_result(
                "p", _result(), NOW
            )

    def test_reconciled_state_write_failure(self) -> None:
        client = _FailingClient(_client_error("InternalServerError", "boom"), "put_item")
        with pytest.raises(StorageError, match="reconciled state"):
            DynamoExecutionState(TABLE_NAME, client=client).write_reconciled_state(
                {"SPY": 1.0}, 100.0, NOW
            )

    def test_reconciled_state_rejects_invalid_input(self) -> None:
        state = _state()
        with pytest.raises(StorageError, match="must not be empty"):
            state.write_reconciled_state({}, 100.0, NOW)
        with pytest.raises(StorageError, match="finite and non-negative"):
            state.write_reconciled_state({"SPY": -1.0}, 100.0, NOW)
        with pytest.raises(StorageError, match="account equity"):
            state.write_reconciled_state({"SPY": 1.1}, 100.0, NOW)
        with pytest.raises(StorageError, match="account value"):
            state.write_reconciled_state({"SPY": 1.0}, 0.0, NOW)

    def test_claim_with_generic_client_error(self) -> None:
        client = _FailingClient(_client_error("InternalServerError", "boom"), "put_item")
        with pytest.raises(StorageError, match="claim idempotency"):
            DynamoExecutionState(TABLE_NAME, client=client).claim_idempotency("key-1", "p", NOW)

    def test_claim_disappears_between_condition_and_read(self) -> None:
        class _GhostClient:
            def put_item(self, **kwargs: Any) -> None:
                raise _client_error("ConditionalCheckFailedException", "exists")

            def get_item(self, **kwargs: Any) -> dict[str, Any]:
                return {}

        with pytest.raises(StorageError, match="disappeared"):
            DynamoExecutionState(TABLE_NAME, client=_GhostClient()).claim_idempotency(
                "key-1", "p", NOW
            )

    def test_claim_with_botocore_error(self) -> None:
        client = _FailingClient(BotoCoreError(), "put_item")
        with pytest.raises(StorageError, match="claim idempotency"):
            DynamoExecutionState(TABLE_NAME, client=client).claim_idempotency("k", "p", NOW)

    def test_idempotency_result_never_claimed(self) -> None:
        client = _FailingClient(
            _client_error("ConditionalCheckFailedException", "absent"), "update_item"
        )
        with pytest.raises(StorageError, match="never claimed"):
            DynamoExecutionState(TABLE_NAME, client=client).write_idempotency_result(
                "key-1", _result(), NOW
            )

    def test_idempotency_result_generic_failure(self) -> None:
        client = _FailingClient(_client_error("InternalServerError", "boom"), "update_item")
        with pytest.raises(StorageError, match="idempotency result"):
            DynamoExecutionState(TABLE_NAME, client=client).write_idempotency_result(
                "key-1", _result(), NOW
            )

    def test_idempotency_result_botocore_error(self) -> None:
        client = _FailingClient(BotoCoreError(), "update_item")
        with pytest.raises(StorageError, match="idempotency result"):
            DynamoExecutionState(TABLE_NAME, client=client).write_idempotency_result(
                "key-1", _result(), NOW
            )

    def test_read_failure(self) -> None:
        client = _FailingClient(_client_error("InternalServerError", "boom"), "get_item")
        with pytest.raises(StorageError, match="read execution state"):
            DynamoExecutionState(TABLE_NAME, client=client).read_proposal("p")


class TestMalformedItems:
    def test_proposal_missing_attributes(self, state_table) -> None:
        state_table.put_item(
            TableName=TABLE_NAME,
            Item={"pk": {"S": "PROPOSAL"}, "sk": {"S": "p-broken"}},
        )
        with pytest.raises(StorageError, match="missing required"):
            _state().read_proposal("p-broken")

    def test_proposal_weights_not_a_map(self, state_table) -> None:
        created = NOW - timedelta(minutes=30)
        state_table.put_item(
            TableName=TABLE_NAME,
            Item={
                "pk": {"S": "PROPOSAL"},
                "sk": {"S": "p-broken"},
                "status": {"S": "PENDING"},
                "w_star": {"S": "not-a-map"},
                "created_at": {"S": created.isoformat()},
                "expires_at": {"S": (created + timedelta(hours=24)).isoformat()},
                "run_date": {"S": "2026-11-13"},
            },
        )
        with pytest.raises(StorageError, match="numeric map"):
            _state().read_proposal("p-broken")

    def test_proposal_weights_not_numeric(self, state_table) -> None:
        created = NOW - timedelta(minutes=30)
        state_table.put_item(
            TableName=TABLE_NAME,
            Item={
                "pk": {"S": "PROPOSAL"},
                "sk": {"S": "p-broken"},
                "status": {"S": "PENDING"},
                "w_star": {"M": {"SPY": {"S": "0.5"}}},
                "created_at": {"S": created.isoformat()},
                "expires_at": {"S": (created + timedelta(hours=24)).isoformat()},
                "run_date": {"S": "2026-11-13"},
            },
        )
        with pytest.raises(StorageError, match="numeric"):
            _state().read_proposal("p-broken")

    def test_claim_with_invalid_stored_result(self, state_table) -> None:
        state_table.put_item(
            TableName=TABLE_NAME,
            Item={
                "pk": {"S": "IDEMPOTENCY"},
                "sk": {"S": "key-1"},
                "proposal_id": {"S": "p"},
                "status": {"S": "FAILED"},
                "result": {"S": "not-json"},
            },
        )
        with pytest.raises(StorageError, match="invalid"):
            _state().claim_idempotency("key-1", "p", NOW)


class TestUnresolvedClaims:
    def test_an_unresolved_claim_is_taken_over_once_and_keeps_its_orders(self, state_table) -> None:
        state = _state()
        state.claim_idempotency(KEY, PROPOSAL_ID, NOW)
        state.write_idempotency_result(
            KEY,
            _result(ProposalStatus.UNRESOLVED, error="deadline reached"),
            NOW,
        )

        first = state.claim_idempotency(KEY, PROPOSAL_ID, NOW + timedelta(minutes=1))
        second = state.claim_idempotency(KEY, PROPOSAL_ID, NOW + timedelta(minutes=2))

        assert first.fresh is True
        assert first.result is not None
        assert first.result.status is ProposalStatus.UNRESOLVED
        # A concurrent retry that loses the conditional update must not resume.
        assert second.fresh is False
        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "IDEMPOTENCY"}, "sk": {"S": KEY}},
        )["Item"]
        assert item["status"]["S"] == "EXECUTING"

    def test_an_unresolved_claim_for_another_proposal_is_not_resumed(self, state_table) -> None:
        state = _state()
        state.claim_idempotency(KEY, PROPOSAL_ID, NOW)
        state.write_idempotency_result(
            KEY,
            _result(ProposalStatus.UNRESOLVED, error="deadline reached"),
            NOW,
        )

        other = state.claim_idempotency(KEY, "another-proposal", NOW + timedelta(minutes=1))

        assert other.fresh is False
        assert other.proposal_id == PROPOSAL_ID

    def test_a_final_claim_is_never_reclaimed(self, state_table) -> None:
        state = _state()
        state.claim_idempotency(KEY, PROPOSAL_ID, NOW)
        state.write_idempotency_result(KEY, _result(ProposalStatus.EXECUTED), NOW)

        again = state.claim_idempotency(KEY, PROPOSAL_ID, NOW + timedelta(minutes=1))

        assert again.fresh is False
        assert again.result is not None
        assert again.result.status is ProposalStatus.EXECUTED


class TestProposalTransitions:
    def test_an_unresolved_proposal_can_be_resumed(self, state_table) -> None:
        _put_proposal(state_table)
        state = _state()
        state.write_execution_result(
            PROPOSAL_ID, _result(ProposalStatus.UNRESOLVED, error="x"), NOW
        )

        state.transition_to_executing(PROPOSAL_ID)

        item = state_table.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert item["status"]["S"] == "EXECUTING"

    def test_a_final_proposal_cannot_be_executed_again(self, state_table) -> None:
        _put_proposal(state_table)
        state = _state()
        state.write_execution_result(PROPOSAL_ID, _result(ProposalStatus.EXECUTED), NOW)

        with pytest.raises(StorageError, match="not PENDING"):
            state.transition_to_executing(PROPOSAL_ID)


class TestStaleClaimTakeover:
    def test_a_stale_executing_claim_is_taken_over_exactly_once(self, state_table) -> None:
        state = _state()
        state.claim_idempotency(KEY, PROPOSAL_ID, NOW)

        takeover = state.claim_idempotency(
            KEY,
            PROPOSAL_ID,
            NOW + timedelta(seconds=120),
            staleness_seconds=90,
        )
        # A concurrent retry loses: the takeover refreshed created_at, so the
        # claim is no longer older than the cutoff.
        again = state.claim_idempotency(
            KEY,
            PROPOSAL_ID,
            NOW + timedelta(seconds=121),
            staleness_seconds=90,
        )

        assert takeover.fresh is True
        assert takeover.stale_takeover is True
        assert takeover.result is None
        assert again.fresh is False

    def test_a_fresh_executing_claim_is_never_taken_over(self, state_table) -> None:
        state = _state()
        state.claim_idempotency(KEY, PROPOSAL_ID, NOW)

        repeat = state.claim_idempotency(
            KEY,
            PROPOSAL_ID,
            NOW + timedelta(seconds=60),
            staleness_seconds=90,
        )

        assert repeat.fresh is False
        assert repeat.stale_takeover is False

    def test_a_stale_claim_of_another_proposal_is_not_taken_over(self, state_table) -> None:
        state = _state()
        state.claim_idempotency(KEY, PROPOSAL_ID, NOW)

        other = state.claim_idempotency(
            KEY,
            "another-proposal",
            NOW + timedelta(seconds=120),
            staleness_seconds=90,
        )

        assert other.fresh is False
        assert other.stale_takeover is False
        assert other.proposal_id == PROPOSAL_ID

    def test_an_executing_proposal_can_only_be_reentered_by_a_stale_takeover(
        self, state_table
    ) -> None:
        _put_proposal(state_table)
        state = _state()
        state.transition_to_executing(PROPOSAL_ID)

        with pytest.raises(StorageError, match="not PENDING"):
            state.transition_to_executing(PROPOSAL_ID)
        state.transition_to_executing(PROPOSAL_ID, allow_executing=True)
