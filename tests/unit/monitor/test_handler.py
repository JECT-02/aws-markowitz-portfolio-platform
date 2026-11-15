from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from common.errors import BrokerError, NotFoundError
from common.models import BrokerPosition
from monitor.handler import MonitorDependencies, run_monitor
from optimizer.core.models import PortfolioMetrics, Proposal

NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)
_RUN_DATE = date(2026, 11, 14)
TABLE_NAME = "portfolio-state"
THRESHOLD = 0.10


class FakeClock:
    def now(self) -> datetime:
        return NOW

    def today(self) -> date:
        return NOW.date()


@dataclass
class FakeBroker:
    market_open: bool = True
    positions: list[BrokerPosition] = field(default_factory=list)
    equity: float = 1000.0
    clock_calls: int = 0

    def is_market_open(self) -> bool:
        self.clock_calls += 1
        return self.market_open

    def get_positions(self) -> list[BrokerPosition]:
        return self.positions

    def get_account_equity(self) -> float:
        return self.equity


@dataclass
class FakeProposalReader:
    proposal: Proposal | None
    calls: int = 0

    def read_latest(self) -> Proposal:
        self.calls += 1
        if self.proposal is None:
            raise NotFoundError("proposal not found for latest.json")
        return self.proposal


def _proposal(weights: dict[str, float]) -> Proposal:
    return Proposal(
        proposal_id="prop-1",
        metadata={
            "run_id": "run-1",
            "run_date": _RUN_DATE.isoformat(),
            "code_version": "abc1234",
            "source": "alpaca",
            "fetched_at": NOW.isoformat(),
        },
        created_at=NOW,
        status="PROPOSED",
        rebalance_required=False,
        weights=weights,
        actual_weights=weights,
        drift_l1=0.0,
        benchmark_weights={"equal_weight": weights},
        metrics={
            "markowitz": PortfolioMetrics(
                annualized_return=0.1,
                annualized_volatility=0.2,
                sharpe_ratio=0.3,
                sortino_ratio=0.4,
                max_drawdown=0.05,
                calmar_ratio=2.0,
                herfindahl_index=0.5,
            )
        },
        covariance_shrinkage=0.1,
        window_days=252,
        prices_ref="run_date=2026-11-14/prices.json",
        excluded={},
        run_date=_RUN_DATE,
    )


def _deps(
    broker: FakeBroker,
    reader: FakeProposalReader,
) -> MonitorDependencies:
    return MonitorDependencies(
        broker=broker,
        proposal_reader=reader,
        state=RecordingState(),
        rebalance_threshold=THRESHOLD,
        clock=FakeClock(),
    )


@dataclass
class RecordingState:
    snapshots: list[tuple[object, datetime]] = field(default_factory=list)

    def write_snapshot(self, snapshot: object, now: datetime) -> None:
        self.snapshots.append((snapshot, now))


def test_closed_market_skips_without_writes() -> None:
    broker = FakeBroker(market_open=False)
    reader = FakeProposalReader(proposal=None)
    state = RecordingState()
    deps = MonitorDependencies(
        broker=broker,
        proposal_reader=reader,
        state=state,
        rebalance_threshold=THRESHOLD,
        clock=FakeClock(),
    )
    result = run_monitor(deps)
    assert result == {"skipped": "market_closed"}
    assert reader.calls == 0
    assert state.snapshots == []


def test_open_market_writes_one_snapshot() -> None:
    broker = FakeBroker(
        positions=[
            BrokerPosition(symbol="SPY", quantity=6.3, price=100.0),
            BrokerPosition(symbol="TLT", quantity=3.7, price=100.0),
        ]
    )
    reader = FakeProposalReader(proposal=_proposal({"SPY": 0.6, "TLT": 0.4}))
    state = RecordingState()
    deps = MonitorDependencies(
        broker=broker,
        proposal_reader=reader,
        state=state,
        rebalance_threshold=THRESHOLD,
        clock=FakeClock(),
    )
    result = run_monitor(deps)
    assert len(state.snapshots) == 1
    snapshot, timestamp = state.snapshots[0]
    assert timestamp == NOW
    assert snapshot.drift_l1 == pytest.approx(0.06)
    assert result["drift_l1"] == pytest.approx(0.06)
    assert result["rebalance_required"] is False


def test_broker_failure_propagates_without_writes() -> None:
    @dataclass
    class BrokenBroker(FakeBroker):
        def is_market_open(self) -> bool:
            raise BrokerError("alpaca market clock request failed")

    reader = FakeProposalReader(proposal=None)
    deps = _deps(BrokenBroker(), reader)
    with pytest.raises(BrokerError):
        run_monitor(deps)
    assert reader.calls == 0


def test_missing_proposal_propagates_without_writes() -> None:
    broker = FakeBroker()
    deps = _deps(broker, FakeProposalReader(proposal=None))
    with pytest.raises(NotFoundError):
        run_monitor(deps)


@mock_aws
def test_end_to_end_run_writes_the_dynamodb_item() -> None:
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
    from monitor.core.state import DynamoMonitorState

    broker = FakeBroker(
        positions=[
            BrokerPosition(symbol="SPY", quantity=6.3, price=100.0),
            BrokerPosition(symbol="TLT", quantity=3.7, price=100.0),
        ]
    )
    reader = FakeProposalReader(proposal=_proposal({"SPY": 0.6, "TLT": 0.4}))
    deps = MonitorDependencies(
        broker=broker,
        proposal_reader=reader,
        state=DynamoMonitorState(TABLE_NAME, client=dynamodb),
        rebalance_threshold=THRESHOLD,
        clock=FakeClock(),
    )
    result = run_monitor(deps)
    assert result["drift_l1"] == pytest.approx(0.06)
    items = dynamodb.query(
        TableName=TABLE_NAME,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": "MONITOR"}},
    )["Items"]
    assert len(items) == 1
    assert float(items[0]["drift_l1"]["N"]) == pytest.approx(0.06)
    assert items[0]["weights_live"]["M"]["SPY"]["N"] == "0.63"
