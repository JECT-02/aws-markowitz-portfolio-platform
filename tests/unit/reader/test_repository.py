from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from common.errors import NotFoundError, StorageError
from common.storage import S3Storage
from reader.core.models import CurrentState
from reader.core.repository import DynamoCurrentState, S3GoldRepository

BUCKET = "gold-test"
TABLE = "PortfolioStateTest"
DOCUMENT = {"proposal_id": "2025-02-03-run-1", "status": "PENDING"}


@pytest.fixture
def gold() -> Iterator[S3GoldRepository]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield S3GoldRepository(S3Storage(client=client), BUCKET)


@pytest.fixture
def state_table() -> Iterator[DynamoCurrentState]:
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
        yield DynamoCurrentState(TABLE, client)


def _put(bucket: str, key: str, payload: dict[str, object]) -> None:
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=bucket, Key=key, Body=json.dumps(payload).encode("utf-8")
    )


def test_latest_proposal_is_loaded(gold: S3GoldRepository) -> None:
    _put(BUCKET, "latest.json", DOCUMENT)
    assert gold.load_proposal(None) == DOCUMENT


def test_proposal_partition_is_loaded(gold: S3GoldRepository) -> None:
    key = "run_date=2025-02-03/proposal.json"
    _put(BUCKET, key, DOCUMENT)
    assert gold.load_proposal(date(2025, 2, 3)) == DOCUMENT


def test_missing_proposal_raises_not_found(gold: S3GoldRepository) -> None:
    with pytest.raises(NotFoundError):
        gold.load_proposal(None)
    with pytest.raises(NotFoundError):
        gold.load_proposal(date(2025, 2, 4))


def test_invalid_json_raises_storage_error(gold: S3GoldRepository) -> None:
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=BUCKET, Key="latest.json", Body=b"{not-json"
    )
    with pytest.raises(StorageError):
        gold.load_proposal(None)


def test_absent_current_state_returns_none(state_table: DynamoCurrentState) -> None:
    assert state_table.read_current_state() is None


def test_current_state_is_parsed(state_table: DynamoCurrentState) -> None:
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "STATE"},
            "sk": {"S": "CURRENT"},
            "weights": {"M": {"SPY": {"N": "0.6"}, "QQQ": {"N": "0.4"}}},
            "reconciled_at": {"S": datetime(2025, 2, 3, 22, 0, tzinfo=UTC).isoformat()},
            "account_value": {"N": "100000.00"},
        },
    )
    current = state_table.read_current_state()
    assert isinstance(current, CurrentState)
    assert current.weights == {"SPY": 0.6, "QQQ": 0.4}
    assert current.account_value == 100_000.0
    assert current.reconciled_at is not None
    assert current.reconciled_at.year == 2025


def test_malformed_state_raises_storage_error(state_table: DynamoCurrentState) -> None:
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "STATE"},
            "sk": {"S": "CURRENT"},
            "weights": {"M": {"SPY": {"S": "0.6"}}},
        },
    )
    with pytest.raises(StorageError):
        state_table.read_current_state()
