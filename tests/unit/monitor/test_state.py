from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from common.errors import StorageError
from monitor.core.drift import DriftSnapshot
from monitor.core.state import MONITOR_PARTITION, DynamoMonitorState

NOW = datetime(2026, 11, 14, 15, 30, 0, tzinfo=UTC)
TABLE_NAME = "portfolio-state"
_SNAPSHOT = DriftSnapshot(
    proposal_id="prop-1",
    run_date=date(2026, 11, 14),
    weights_live={"SPY": 0.5, "TLT": 0.5},
    drift_l1=0.1,
    rebalance_required=True,
    computed_at=NOW,
)


@mock_aws
def test_writes_one_item_per_timestamp() -> None:
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
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
    state = DynamoMonitorState(TABLE_NAME, client=dynamodb)

    state.write_snapshot(_SNAPSHOT, NOW)
    state.write_snapshot(_SNAPSHOT, NOW)

    response = dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": MONITOR_PARTITION}},
    )
    items = response["Items"]
    assert len(items) == 1
    item = items[0]
    assert item["sk"]["S"] == NOW.isoformat()
    assert item["weights_live"]["M"]["SPY"]["N"] == "0.5"
    assert item["weights_live"]["M"]["TLT"]["N"] == "0.5"
    assert item["drift_l1"]["N"] == "0.1"
    assert item["rebalance_required"]["BOOL"] is True
    assert item["proposal_id"]["S"] == "prop-1"
    assert item["run_date"]["S"] == "2026-11-14"
    expected_ttl = NOW + timedelta(days=7)
    assert int(item["ttl"]["N"]) == int(expected_ttl.timestamp())


@mock_aws
def test_storage_failure_raises_storage_error() -> None:
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    state = DynamoMonitorState(TABLE_NAME, client=dynamodb)
    with pytest.raises(StorageError):
        state.write_snapshot(_SNAPSHOT, NOW)
