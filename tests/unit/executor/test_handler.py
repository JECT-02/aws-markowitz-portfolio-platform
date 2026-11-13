from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from common.config import ExecutorConfig
from common.errors import (
    MarketClosed,
    NotFoundError,
    PriceDriftExceeded,
    ProposalExpired,
    ProposalNotPending,
)
from common.storage import S3Storage
from executor.core.execution import RebalanceService
from executor.core.models import (
    ExecutionResult,
    ProposalStatus,
    Reconciliation,
)
from executor.core.prices import GoldPricesRepository
from executor.core.simulated import SimulatedBroker
from executor.core.state import DynamoExecutionState
from executor.handler import ExecutorDependencies, run_executor

NOW = datetime(2026, 11, 13, 21, 0, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


def _event(
    route: str = "POST /rebalance",
    proposal_id: str = "2026-11-13-run-abc",
    idempotency_key: str | None = "key-1",
) -> dict[str, Any]:
    headers = {"idempotency-key": idempotency_key} if idempotency_key else {}
    body = {"proposal_id": proposal_id} if proposal_id else {}
    return {
        "routeKey": route,
        "headers": headers,
        "body": json.dumps(body),
    }


def _deps(service: Any) -> ExecutorDependencies:
    return ExecutorDependencies(service=service)  # type: ignore[arg-type]


def _result() -> ExecutionResult:
    return ExecutionResult(
        proposal_id="2026-11-13-run-abc",
        status=ProposalStatus.EXECUTED,
        orders=[],
        reconciliation=Reconciliation(
            weights={"SPY": 1.0},
            account_value=1_000.0,
            reconciled_at=NOW,
        ),
    )


class _OkService:
    def execute(self, proposal_id: str, idempotency_key: str) -> ExecutionResult:
        return _result()


class _RejectingService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, proposal_id: str, idempotency_key: str) -> ExecutionResult:
        raise self.error


def test_unknown_route_is_not_found() -> None:
    response = run_executor(_event(route="GET /rebalance"), _deps(_OkService()))
    assert response["statusCode"] == 404


def test_missing_idempotency_key_is_a_client_error() -> None:
    response = run_executor(_event(idempotency_key=None), _deps(_OkService()))
    assert response["statusCode"] == 400
    assert "idempotency" in response["body"]


def test_invalid_body_is_a_client_error() -> None:
    event = {"routeKey": "POST /rebalance", "headers": {"idempotency-key": "k"}, "body": "not-json"}
    response = run_executor(event, _deps(_OkService()))
    assert response["statusCode"] == 400


def test_missing_proposal_id_is_a_client_error() -> None:
    event = {"routeKey": "POST /rebalance", "headers": {"idempotency-key": "k"}, "body": "{}"}
    response = run_executor(event, _deps(_OkService()))
    assert response["statusCode"] == 400


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (NotFoundError("proposal missing"), 404),
        (ProposalExpired("expired"), 409),
        (ProposalNotPending("not pending"), 409),
        (MarketClosed("closed"), 409),
        (PriceDriftExceeded("drift"), 409),
    ],
)
def test_rejections_map_to_http_errors(error: Exception, status_code: int) -> None:
    response = run_executor(_event(), _deps(_RejectingService(error)))
    assert response["statusCode"] == status_code


def test_success_returns_the_result() -> None:
    response = run_executor(_event(), _deps(_OkService()))
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["proposal_id"] == "2026-11-13-run-abc"
    assert body["status"] == "EXECUTED"
    assert body["reconciliation"]["account_value"] == 1000.0


@mock_aws
def test_end_to_end_flow_through_the_handler() -> None:
    client = _create_state_table()
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="gold")
    _put_pending_proposal(client)
    s3.put_object(
        Bucket="gold",
        Key="run_date=2026-11-13/prices.json",
        Body=b'{"prices": {"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0}}',
    )
    service = RebalanceService(
        state=DynamoExecutionState("portfolio-state", region_name="us-east-1"),
        broker=SimulatedBroker(
            positions=[],
            equity=100_000.0,
            prices={"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
        ),
        prices=GoldPricesRepository(S3Storage(s3), "gold"),
        config=_executor_config(),
        clock=FixedClock(NOW),
    )
    response = run_executor(_event(), _deps(service))
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "EXECUTED"
    assert body["reconciliation"] is not None


def _executor_config() -> ExecutorConfig:
    return ExecutorConfig.model_validate(
        {
            "min_order_notional": 50.0,
            "max_price_drift_pct": 0.01,
            "execution_timeout_seconds": 60,
        }
    )


def _create_state_table() -> Any:
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(
        TableName="portfolio-state",
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
    return client


def _put_pending_proposal(client: Any) -> None:
    created = NOW - timedelta(minutes=30)
    client.put_item(
        TableName="portfolio-state",
        Item={
            "pk": {"S": "PROPOSAL"},
            "sk": {"S": "2026-11-13-run-abc"},
            "status": {"S": "PENDING"},
            "w_star": {
                "M": {
                    "SPY": {"N": "0.2"},
                    "QQQ": {"N": "0.2"},
                    "IWM": {"N": "0.2"},
                    "EFA": {"N": "0.2"},
                    "TLT": {"N": "0.2"},
                }
            },
            "created_at": {"S": created.isoformat()},
            "expires_at": {"S": (created + timedelta(hours=24)).isoformat()},
            "run_date": {"S": "2026-11-13"},
        },
    )
