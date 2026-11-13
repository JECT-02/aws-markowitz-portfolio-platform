from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from reader.handler import ReaderDependencies, build_dependencies, run_reader

BUCKET = "gold-test"
TABLE = "PortfolioStateTest"


def _proposal_document() -> dict[str, Any]:
    return {
        "proposal_id": "2025-02-03-run-1",
        "metadata": {
            "run_id": "run-1",
            "run_date": "2025-02-03",
            "code_version": "abc123",
            "source": "alpaca",
            "fetched_at": "2025-02-03T21:30:00Z",
        },
        "created_at": "2025-02-03T21:45:00Z",
        "status": "PENDING",
        "rebalance_required": True,
        "weights": {"SPY": 0.6, "QQQ": 0.4},
        "actual_weights": {"SPY": 0.5, "QQQ": 0.5},
        "drift_l1": 0.2,
        "benchmark_weights": {},
        "metrics": {},
        "covariance_shrinkage": 0.1,
        "window_days": 252,
        "prices_ref": "2024-02-01..2025-01-31",
        "excluded": {},
        "run_date": "2025-02-03",
    }


@pytest.fixture
def environment() -> Iterator[None]:
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(
            Bucket=BUCKET,
            Key="latest.json",
            Body=json.dumps(_proposal_document()).encode("utf-8"),
        )
        dynamo = boto3.client("dynamodb", region_name="us-east-1")
        dynamo.create_table(
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
        os.environ["AWS_REGION"] = "us-east-1"
        os.environ["CODE_VERSION"] = "test"
        os.environ["CONFIG_DIR"] = "config"
        os.environ["BRONZE_BUCKET"] = "bronze"
        os.environ["QUARANTINE_BUCKET"] = "quarantine"
        os.environ["SILVER_BUCKET"] = "silver"
        os.environ["GOLD_BUCKET"] = BUCKET
        os.environ["STATE_TABLE"] = TABLE
        yield
        for name in (
            "AWS_REGION",
            "CODE_VERSION",
            "CONFIG_DIR",
            "BRONZE_BUCKET",
            "QUARANTINE_BUCKET",
            "SILVER_BUCKET",
            "GOLD_BUCKET",
            "STATE_TABLE",
        ):
            os.environ.pop(name, None)


def _deps() -> ReaderDependencies:
    return build_dependencies()


def _event(route: str, parameters: dict[str, str] | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"routeKey": route}
    if parameters is not None:
        event["queryStringParameters"] = parameters
    return event


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def test_get_proposal_returns_latest(environment: None) -> None:
    response = run_reader(_event("GET /proposal"), _deps())
    assert response["statusCode"] == 200
    assert response["headers"]["content-type"] == "application/json"
    body = _body(response)
    assert body["proposal_id"] == "2025-02-03-run-1"
    assert body["run_date"] == "2025-02-03"


def test_get_proposal_by_run_date_uses_the_partition(environment: None) -> None:
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=BUCKET,
        Key="run_date=2025-02-03/proposal.json",
        Body=json.dumps(_proposal_document()).encode("utf-8"),
    )
    response = run_reader(_event("GET /proposal", {"run_date": "2025-02-03"}), _deps())
    assert response["statusCode"] == 200
    assert _body(response)["proposal_id"] == "2025-02-03-run-1"


def test_missing_partition_returns_404(environment: None) -> None:
    response = run_reader(_event("GET /proposal", {"run_date": "2025-02-04"}), _deps())
    assert response["statusCode"] == 404
    assert "proposal" in _body(response)["message"]


def test_get_metrics_returns_metrics_map(environment: None) -> None:
    response = run_reader(_event("GET /metrics"), _deps())
    assert response["statusCode"] == 200
    assert _body(response) == {"metrics": {}}


def test_get_monitor_falls_back_to_proposal_weights(environment: None) -> None:
    response = run_reader(_event("GET /monitor"), _deps())
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["weights"] == {"SPY": 0.5, "QQQ": 0.5}
    assert body["drift_l1"] == 0.2
    assert body["proposal_id"] == "2025-02-03-run-1"
    assert body["reconciled_at"] is None


def test_get_monitor_uses_reconciled_state(environment: None) -> None:
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=TABLE,
        Item={
            "pk": {"S": "STATE"},
            "sk": {"S": "CURRENT"},
            "weights": {"M": {"SPY": {"N": "0.7"}, "QQQ": {"N": "0.3"}}},
            "reconciled_at": {"S": datetime(2025, 2, 3, 22, 0, tzinfo=UTC).isoformat()},
        },
    )
    response = run_reader(_event("GET /monitor"), _deps())
    body = _body(response)
    assert response["statusCode"] == 200
    assert body["weights"] == {"SPY": 0.7, "QQQ": 0.3}
    assert body["reconciled_at"] is not None


def test_get_backtest_returns_404(environment: None) -> None:
    response = run_reader(_event("GET /backtest"), _deps())
    assert response["statusCode"] == 404
    assert "phase 6" in _body(response)["message"]


def test_unknown_route_returns_404(environment: None) -> None:
    response = run_reader(_event("POST /proposal"), _deps())
    assert response["statusCode"] == 404


def test_invalid_run_date_returns_400(environment: None) -> None:
    response = run_reader(_event("GET /proposal", {"run_date": "03-02-2025"}), _deps())
    assert response["statusCode"] == 400
    assert "ISO date" in _body(response)["message"]


def test_empty_run_date_parameter_is_ignored(environment: None) -> None:
    response = run_reader(_event("GET /proposal", {"run_date": "  "}), _deps())
    assert response["statusCode"] == 200
