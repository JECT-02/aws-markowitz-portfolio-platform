from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from common.errors import DataValidationError
from common.models import DataSource, RunMetadata
from optimizer.core.models import Proposal
from optimizer.core.state_adapter import (
    DynamoPortfolioState,
    proposal_identifier,
)

TABLE = "PortfolioStateTest"


@pytest.fixture
def state() -> Iterator[DynamoPortfolioState]:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield DynamoPortfolioState(TABLE, client)


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-1",
        run_date=date(2025, 2, 3),
        code_version="abc123",
        source=DataSource.ALPACA,
        fetched_at=datetime(2025, 2, 3, 21, 30, tzinfo=UTC),
    )


def _proposal(metadata: RunMetadata) -> Proposal:
    return Proposal(
        proposal_id="2025-02-03-run-1",
        metadata=metadata,
        created_at=datetime(2025, 2, 3, 21, 45, tzinfo=UTC),
        status="PENDING",
        rebalance_required=True,
        weights={"SPY": 0.6, "QQQ": 0.4},
        actual_weights={"SPY": 0.5, "QQQ": 0.5},
        drift_l1=0.2,
        benchmark_weights={},
        metrics={},
        covariance_shrinkage=0.1,
        window_days=252,
        excluded={},
        run_date=metadata.run_date,
    )


def test_missing_state_returns_none(state: DynamoPortfolioState) -> None:
    assert state.read_actual_weights() is None


def test_reads_current_weights(state: DynamoPortfolioState) -> None:
    state._client.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "STATE"},
            "sk": {"S": "CURRENT"},
            "weights": {"M": {"SPY": {"N": "0.6"}, "QQQ": {"N": "0.4"}}},
        },
    )
    assert state.read_actual_weights() == {"SPY": 0.6, "QQQ": 0.4}


def test_rejects_weights_that_do_not_sum_to_one(state: DynamoPortfolioState) -> None:
    state._client.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "STATE"},
            "sk": {"S": "CURRENT"},
            "weights": {"M": {"SPY": {"N": "0.6"}}},
        },
    )
    with pytest.raises(DataValidationError):
        state.read_actual_weights()


def test_rejects_non_numeric_weights(state: DynamoPortfolioState) -> None:
    state._client.put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "STATE"},
            "sk": {"S": "CURRENT"},
            "weights": {"M": {"SPY": {"S": "0.6"}}},
        },
    )
    with pytest.raises(DataValidationError):
        state.read_actual_weights()


def test_proposal_is_persisted_with_decimals(state: DynamoPortfolioState) -> None:
    metadata = _metadata()
    state.write_proposal(_proposal(metadata))
    item = state._client.get_item(
        TableName=TABLE,
        Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": "2025-02-03-run-1"}},
    )["Item"]
    assert item["status"]["S"] == "PENDING"
    assert item["w_star"]["M"]["SPY"]["N"] == "0.6"
    assert item["drift_l1"]["N"] == "0.2"
    assert item["rebalance_required"]["BOOL"] is True
    assert int(item["ttl"]["N"]) == 1738705500
    assert item["run_id"]["S"] == "run-1"


def test_duplicate_proposal_keeps_the_first_version(state: DynamoPortfolioState) -> None:
    metadata = _metadata()
    state.write_proposal(_proposal(metadata))
    state.write_failure(proposal_identifier(metadata), metadata, "solver failed")
    item = state._client.get_item(
        TableName=TABLE,
        Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": "2025-02-03-run-1"}},
    )["Item"]
    assert item["status"]["S"] == "PENDING"


def test_failure_is_persisted_without_weights(state: DynamoPortfolioState) -> None:
    metadata = _metadata()
    state.write_failure(proposal_identifier(metadata), metadata, "infeasible")
    item = state._client.get_item(
        TableName=TABLE,
        Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": "2025-02-03-run-1"}},
    )["Item"]
    assert item["status"]["S"] == "FAILED"
    assert item["error"]["S"] == "infeasible"
    assert "w_star" not in item


def test_non_finite_drift_is_rejected(state: DynamoPortfolioState) -> None:
    metadata = _metadata()
    proposal = _proposal(metadata).model_copy(update={"drift_l1": float("nan")})
    with pytest.raises(DataValidationError):
        state.write_proposal(proposal)


def test_proposal_identifier_is_deterministic() -> None:
    assert proposal_identifier(_metadata()) == "2025-02-03-run-1"
