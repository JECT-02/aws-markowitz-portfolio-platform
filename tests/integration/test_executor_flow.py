from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from common.config import ExecutorConfig
from common.errors import (
    IdempotencyConflict,
    MarketClosed,
    NotFoundError,
    PriceDriftExceeded,
    ProposalExpired,
    ProposalNotPending,
)
from common.storage import S3Storage
from executor.core.execution import RebalanceService
from executor.core.models import BrokerPosition, ProposalStatus
from executor.core.prices import GoldPricesRepository
from executor.core.simulated import SimulatedBroker
from executor.core.state import DynamoExecutionState

TABLE_NAME = "portfolio-state"
NOW = datetime(2026, 11, 13, 21, 0, 0, tzinfo=UTC)
PROPOSAL_ID = "2026-11-13-run-abc"
KEY = "idem-key-1"
SYMBOLS = ("SPY", "QQQ", "IWM", "EFA", "TLT")


class SteppingClock:
    """Clock that advances on every read, so polling reaches the deadline."""

    def __init__(self, start: datetime, step_seconds: float = 1.0) -> None:
        self._moment = start
        self._step = timedelta(seconds=step_seconds)

    def now(self) -> datetime:
        self._moment += self._step
        return self._moment

    def today(self) -> date:
        return self._moment.date()


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


def _executor_config() -> ExecutorConfig:
    return ExecutorConfig.model_validate(
        {
            "min_order_notional": 50.0,
            "max_price_drift_pct": 0.01,
            "execution_timeout_seconds": 60,
        }
    )


def _put_proposal(client: Any, status: str = "PENDING") -> None:
    created = NOW - timedelta(minutes=30)
    client.put_item(
        TableName=TABLE_NAME,
        Item={
            "pk": {"S": "PROPOSAL"},
            "sk": {"S": PROPOSAL_ID},
            "status": {"S": status},
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


def _put_prices(client: Any, bucket: str = "gold") -> None:
    client.put_object(
        Bucket=bucket,
        Key="run_date=2026-11-13/prices.json",
        Body=b'{"prices": {"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0}}',
    )


def _broker(**overrides: Any) -> SimulatedBroker:
    defaults: dict[str, Any] = {
        "positions": [
            BrokerPosition(symbol=symbol, quantity=100.0, price=price)
            for symbol, price in (
                ("SPY", 100.0),
                ("QQQ", 50.0),
                ("IWM", 40.0),
                ("EFA", 80.0),
            )
        ],
        "equity": 100_000.0,
        "prices": {"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
    }
    defaults.update(overrides)
    return SimulatedBroker(**defaults)


@pytest.fixture
def env():  # type: ignore[no-untyped-def]
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
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="gold")
        yield client, s3


def _service(broker: SimulatedBroker, clock: Any = None) -> RebalanceService:
    return RebalanceService(
        state=DynamoExecutionState(TABLE_NAME, region_name="us-east-1"),
        broker=broker,
        prices=GoldPricesRepository(S3Storage(boto3.client("s3", region_name="us-east-1")), "gold"),
        config=_executor_config(),
        clock=clock if clock is not None else FixedClock(NOW),
    )


class TestExecuteHappyPath:
    def test_executes_and_reconciles(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker()
        result = _service(broker).execute(PROPOSAL_ID, KEY)
        assert result.status is ProposalStatus.EXECUTED
        assert all(order.status.value == "filled" for order in result.orders)
        assert result.reconciliation is not None
        total = sum(result.reconciliation.weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)
        assert set(result.reconciliation.weights) == set(SYMBOLS)

    def test_sells_are_submitted_before_buys(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker(
            positions=[
                BrokerPosition(symbol="SPY", quantity=700.0, price=100.0),
                BrokerPosition(symbol="QQQ", quantity=10.0, price=50.0),
            ]
        )
        _service(broker).execute(PROPOSAL_ID, KEY)
        submitted = broker.submitted
        assert submitted[0][2].value == "sell"
        assert submitted[0][1] == "SPY"
        assert all(entry[2].value == "buy" for entry in submitted[1:])

    def test_state_is_reconciled_after_execution(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        _service(_broker()).execute(PROPOSAL_ID, KEY)
        state_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        assert "weights" in state_item
        assert "reconciled_at" in state_item
        proposal_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert proposal_item["status"]["S"] == "EXECUTED"

    def test_repeated_key_replays_the_recorded_result(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker()
        service = _service(broker)
        first = service.execute(PROPOSAL_ID, KEY)
        submitted_after_first = len(broker.submitted)
        second = service.execute(PROPOSAL_ID, KEY)
        assert second == first
        assert len(broker.submitted) == submitted_after_first


class TestRejections:
    def test_unknown_proposal_is_not_found(self, env) -> None:
        client, s3 = env
        _put_prices(s3)
        with pytest.raises(NotFoundError):
            _service(_broker()).execute("missing-proposal", KEY)

    def test_expired_proposal_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker()
        expired = datetime.now(UTC) + timedelta(days=2)
        with pytest.raises(ProposalExpired):
            _service(broker, FixedClock(expired)).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_non_pending_proposal_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client, status="NO_REBALANCE")
        _put_prices(s3)
        broker = _broker()
        with pytest.raises(ProposalNotPending):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_market_closed_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker(market_open=False)
        with pytest.raises(MarketClosed):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_price_drift_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker(prices={"SPY": 200.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0})
        with pytest.raises(PriceDriftExceeded):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_missing_prices_fail_closed(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker()
        with pytest.raises(NotFoundError, match="prices"):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_in_flight_claim_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        client.put_item(
            TableName=TABLE_NAME,
            Item={
                "pk": {"S": "IDEMPOTENCY"},
                "sk": {"S": KEY},
                "proposal_id": {"S": PROPOSAL_ID},
                "status": {"S": "EXECUTING"},
                "ttl": {"N": str(int((NOW + timedelta(hours=24)).timestamp()))},
            },
        )
        broker = _broker()
        with pytest.raises(IdempotencyConflict):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []


class TestPartialFills:
    def test_partial_fill_sets_partial_status_and_reconciles(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker(partial_fill_ratio={"TLT": 0.5})
        clock = SteppingClock(NOW, step_seconds=10)
        result = _service(broker, clock).execute(PROPOSAL_ID, KEY)
        assert result.status is ProposalStatus.PARTIAL
        assert result.reconciliation is not None
        partial = [order for order in result.orders if order.status.value == "partially_filled"]
        assert len(partial) == 1
        assert partial[0].filled_quantity < partial[0].requested_quantity

    def test_rejected_all_orders_fail_the_execution(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        _put_prices(s3)
        broker = _broker(reject_symbols=set(SYMBOLS))
        clock = SteppingClock(NOW, step_seconds=10)
        result = _service(broker, clock).execute(PROPOSAL_ID, KEY)
        assert result.status is ProposalStatus.FAILED
        assert result.error is not None
