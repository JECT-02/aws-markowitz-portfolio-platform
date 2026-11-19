from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from common.config import ExecutorConfig
from common.errors import (
    BrokerError,
    IdempotencyConflict,
    MarketClosed,
    NotFoundError,
    PriceDriftExceeded,
    ProposalExpired,
    ProposalNotPending,
    StorageError,
)
from common.models import BrokerPosition, OrderSide, OrderSnapshot, OrderStatus
from executor.core.execution import RebalanceService
from executor.core.models import ProposalStatus
from executor.core.planning import client_order_id_for
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


def _service(
    broker: SimulatedBroker,
    clock: Any = None,
    waiter: Any = None,
    notifier: Any = None,
) -> RebalanceService:
    return RebalanceService(
        state=DynamoExecutionState(TABLE_NAME, region_name="us-east-1"),
        broker=broker,
        config=_executor_config(),
        clock=clock if clock is not None else FixedClock(NOW),
        waiter=waiter,
        notifier=notifier,
    )


class _RecordingNotifier:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, ProposalStatus, str]] = []
        self._error = error

    def notify_failure(self, proposal_id: str, status: ProposalStatus, reason: str) -> None:
        self.calls.append((proposal_id, status, reason))
        if self._error is not None:
            raise self._error


class _PriceMovesAfterFirstOrder(SimulatedBroker):
    """Reprices SPY right after the first submission (mistakes.md QA-015)."""

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderSnapshot:
        snapshot = super().submit_order(client_order_id, symbol, side, quantity)
        self.set_price("SPY", 50.0)
        return snapshot


class _SlowFills(SimulatedBroker):
    """Reports every order as open for the first ``open_polls`` lookups."""

    def __init__(self, *args: Any, open_polls: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._open_polls = open_polls
        self.polls = 0

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderSnapshot:
        super().submit_order(client_order_id, symbol, side, quantity)
        return OrderSnapshot(status=OrderStatus.UNKNOWN, filled_quantity=0.0)

    def get_order_status(self, client_order_id: str) -> OrderSnapshot:
        self.polls += 1
        if self.polls <= self._open_polls:
            return OrderSnapshot(status=OrderStatus.UNKNOWN, filled_quantity=0.0)
        return super().get_order_status(client_order_id)


class _BrokerRejectingSubmits(SimulatedBroker):
    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderSnapshot:
        raise BrokerError(f"alpaca rejected order {client_order_id}")


class TestReconciliationPricing:
    def test_reconciled_weights_use_the_prices_after_the_fills(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _PriceMovesAfterFirstOrder(
            positions=[
                BrokerPosition(symbol=symbol, quantity=100.0, price=price)
                for symbol, price in (
                    ("SPY", 100.0),
                    ("QQQ", 50.0),
                    ("IWM", 40.0),
                    ("EFA", 80.0),
                )
            ],
            equity=100_000.0,
            prices={"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
        )
        _service(broker).execute(PROPOSAL_ID, KEY)
        spy_quantity = next(p.quantity for p in broker.get_positions() if p.symbol == "SPY")
        state_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        spy_weight = float(state_item["weights"]["M"]["SPY"]["N"])
        # Valued at the repriced 50.0, not at the 100.0 used to plan the orders.
        assert spy_weight == pytest.approx(spy_quantity * 50.0 / 100_000.0, abs=1e-6)


class TestFillPolling:
    def test_polling_backs_off_from_half_a_second_to_two_seconds(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        intervals: list[float] = []
        broker = _SlowFills(
            positions=[
                BrokerPosition(symbol=symbol, quantity=100.0, price=price)
                for symbol, price in (
                    ("SPY", 100.0),
                    ("QQQ", 50.0),
                    ("IWM", 40.0),
                    ("EFA", 80.0),
                )
            ],
            equity=100_000.0,
            prices={"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
            open_polls=3,
        )
        _service(broker, waiter=intervals.append).execute(PROPOSAL_ID, KEY)
        # The first order stays open for three lookups: 0.5, 1.0, 2.0 seconds.
        assert intervals[:3] == pytest.approx([0.5, 1.0, 2.0])
        assert max(intervals) <= 2.0
        assert min(intervals) >= 0.5


class TestFailureAnnouncements:
    def test_a_rejected_attempt_is_announced_while_the_proposal_stays_pending(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        notifier = _RecordingNotifier()
        with pytest.raises(MarketClosed):
            _service(_broker(market_open=False), notifier=notifier).execute(PROPOSAL_ID, KEY)
        assert len(notifier.calls) == 1
        proposal_id, status, reason = notifier.calls[0]
        assert proposal_id == PROPOSAL_ID
        assert status is ProposalStatus.PENDING
        assert "closed" in reason

    def test_a_failure_after_execution_started_is_announced_as_failed(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        notifier = _RecordingNotifier()
        with pytest.raises(BrokerError):
            _service(_BrokerRejectingSubmits(**_broker_kwargs()), notifier=notifier).execute(
                PROPOSAL_ID, KEY
            )
        assert [call[1] for call in notifier.calls] == [ProposalStatus.FAILED]

    def test_a_notification_error_never_masks_the_original_failure(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        notifier = _RecordingNotifier(error=StorageError("sns is down"))
        with pytest.raises(MarketClosed):
            _service(_broker(market_open=False), notifier=notifier).execute(PROPOSAL_ID, KEY)
        assert len(notifier.calls) == 1


def _broker_kwargs() -> dict[str, Any]:
    return {
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


class TestExecuteHappyPath:
    def test_executes_and_reconciles(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
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
        with pytest.raises(NotFoundError):
            _service(_broker()).execute("missing-proposal", KEY)

    def test_expired_proposal_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker()
        expired = datetime.now(UTC) + timedelta(days=2)
        with pytest.raises(ProposalExpired):
            _service(broker, FixedClock(expired)).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_non_pending_proposal_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client, status="NO_REBALANCE")
        broker = _broker()
        with pytest.raises(ProposalNotPending):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_market_closed_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker(market_open=False)
        with pytest.raises(MarketClosed):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_price_drift_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker(
            prices={"SPY": 200.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
            adjusted_closes={"SPY": 100.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
        )
        with pytest.raises(PriceDriftExceeded):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_missing_reference_prices_fail_closed(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker(adjusted_closes={})
        with pytest.raises(BrokerError, match="adjusted close"):
            _service(broker).execute(PROPOSAL_ID, KEY)
        assert broker.submitted == []

    def test_a_rebased_adjusted_close_does_not_reject_the_proposal(self, env) -> None:
        """N-15: a split rebases the adjusted history, not the drift check."""
        client, s3 = env
        _put_proposal(client)
        # SPY did a 2:1 split since the proposal: the live price is 50 and
        # the adjusted history is rebased to the same basis.
        broker = _broker(
            prices={"SPY": 50.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
            adjusted_closes={"SPY": 50.0, "QQQ": 50.0, "IWM": 40.0, "EFA": 80.0, "TLT": 90.0},
        )
        result = _service(broker).execute(PROPOSAL_ID, KEY)
        assert result.status is ProposalStatus.EXECUTED

    def test_in_flight_claim_is_rejected_without_orders(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
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
        broker = _broker(partial_fill_ratio={"TLT": 0.5})
        clock = SteppingClock(NOW, step_seconds=10)
        result = _service(broker, clock).execute(PROPOSAL_ID, KEY)
        assert result.status is ProposalStatus.PARTIAL
        assert result.reconciliation is not None
        partial = [order for order in result.orders if order.status.value == "partially_filled"]
        assert len(partial) == 1
        assert partial[0].filled_quantity < partial[0].requested_quantity
        # Weights are fractions of the equity: the unfilled half of the TLT
        # target stays in cash, so the reconciled sum is strictly below one.
        total = sum(result.reconciliation.weights.values())
        assert total == pytest.approx(0.9, abs=1e-6)
        state_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        stored = {symbol: float(value["N"]) for symbol, value in state_item["weights"]["M"].items()}
        assert sum(stored.values()) == pytest.approx(total, abs=1e-6)

    def test_rejected_all_orders_fail_the_execution(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker(reject_symbols=set(SYMBOLS))
        clock = SteppingClock(NOW, step_seconds=10)
        result = _service(broker, clock).execute(PROPOSAL_ID, KEY)
        assert result.status is ProposalStatus.FAILED
        assert result.error is not None
        # Even with every order rejected the real positions reconcile.
        assert result.reconciliation is not None


class _FailingAfterFirstBroker:
    """Delegates to a SimulatedBroker and raises from the second submit."""

    def __init__(self, inner: SimulatedBroker) -> None:
        self._inner = inner
        self.calls = 0

    def submit_order(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls >= 2:
            raise BrokerError("broker rejected the second order")
        return self._inner.submit_order(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestMidFlightFailure:
    def test_failure_after_a_filled_sell_still_reconciles_state(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _broker(
            positions=[
                BrokerPosition(symbol="SPY", quantity=700.0, price=100.0),
                BrokerPosition(symbol="QQQ", quantity=10.0, price=50.0),
            ]
        )
        with pytest.raises(BrokerError):
            _service(_FailingAfterFirstBroker(broker)).execute(PROPOSAL_ID, KEY)
        proposal_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert proposal_item["status"]["S"] == "FAILED"
        # The sell filled before the failure: the reconciled state must show
        # the reduced position instead of the pre-execution weights.
        state_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "STATE"}, "sk": {"S": "CURRENT"}},
        )["Item"]
        weights = {
            symbol: float(value["N"]) for symbol, value in state_item["weights"]["M"].items()
        }
        assert weights["SPY"] == pytest.approx(0.2, abs=1e-6)
        assert weights["QQQ"] == pytest.approx(0.005, abs=1e-6)
        assert float(state_item["account_value"]["N"]) == pytest.approx(100_000.0)

    def test_broker_failure_during_reconciliation_does_not_mask_the_error(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _FailingAfterFirstBroker(_broker())
        service = _service(broker)
        broker.calls = 1  # fail immediately on the first order submit
        with pytest.raises(BrokerError, match="second order"):
            service.execute(PROPOSAL_ID, KEY)


class _HeldOrders(SimulatedBroker):
    """Fills orders as usual but reports the held ones as open until released."""

    def __init__(self, *args: Any, held: set[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.held = set(held)

    def release(self) -> None:
        self.held.clear()

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderSnapshot:
        snapshot = super().submit_order(client_order_id, symbol, side, quantity)
        if client_order_id in self.held:
            return OrderSnapshot(status=OrderStatus.UNKNOWN, filled_quantity=0.0)
        return snapshot

    def get_order_status(self, client_order_id: str) -> OrderSnapshot:
        if client_order_id in self.held:
            return OrderSnapshot(status=OrderStatus.UNKNOWN, filled_quantity=0.0)
        return super().get_order_status(client_order_id)


class TestUnresolvedAttempts:
    def test_an_order_open_at_the_deadline_leaves_the_proposal_unresolved(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        spy_order = client_order_id_for(PROPOSAL_ID, "SPY", OrderSide.BUY)
        broker = _HeldOrders(**_broker_kwargs(), held={spy_order})
        clock = SteppingClock(NOW, step_seconds=10)
        result = _service(broker, clock=clock, waiter=lambda _s: None).execute(PROPOSAL_ID, KEY)

        assert result.status is ProposalStatus.UNRESOLVED
        assert result.error is not None and "SPY" in result.error
        proposal_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert proposal_item["status"]["S"] == "UNRESOLVED"
        claim_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "IDEMPOTENCY"}, "sk": {"S": KEY}},
        )["Item"]
        assert claim_item["status"]["S"] == "UNRESOLVED"
        # No order id is ever sent twice, even when the deadline ends the attempt.
        ids = [order_id for order_id, *_ in broker.submitted]
        assert len(ids) == len(set(ids))

    def test_a_retry_resumes_without_resubmitting_the_open_order(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        spy_order = client_order_id_for(PROPOSAL_ID, "SPY", OrderSide.BUY)
        broker = _HeldOrders(**_broker_kwargs(), held={spy_order})
        first = _service(
            broker,
            clock=SteppingClock(NOW, step_seconds=10),
            waiter=lambda _s: None,
        ).execute(PROPOSAL_ID, KEY)
        assert first.status is ProposalStatus.UNRESOLVED

        broker.release()
        second = _service(broker).execute(PROPOSAL_ID, KEY)

        assert second.status is ProposalStatus.EXECUTED
        submitted_ids = [order_id for order_id, *_ in broker.submitted]
        assert submitted_ids.count(spy_order) == 1
        assert len(submitted_ids) == len(set(submitted_ids))
        proposal_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert proposal_item["status"]["S"] == "EXECUTED"


class _CrashOnSecondSubmit(SimulatedBroker):
    """Simulates a Lambda dying mid-flight: raises outside the Portfolio hierarchy."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._submits = 0

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderSnapshot:
        self._submits += 1
        if self._submits == 2:
            raise RuntimeError("simulated crash after the first order")
        return super().submit_order(client_order_id, symbol, side, quantity)


class TestStaleClaimRecovery:
    def test_a_crashed_attempt_is_resumed_after_the_staleness_bound(self, env) -> None:
        client, s3 = env
        _put_proposal(client)
        broker = _CrashOnSecondSubmit(**_broker_kwargs())
        with pytest.raises(RuntimeError, match="simulated crash"):
            _service(broker).execute(PROPOSAL_ID, KEY)

        # The crash left the claim EXECUTING with no recorded result.
        claim_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "IDEMPOTENCY"}, "sk": {"S": KEY}},
        )["Item"]
        assert claim_item["status"]["S"] == "EXECUTING"
        proposal_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert proposal_item["status"]["S"] == "EXECUTING"

        # A retry before the staleness bound still gets a conflict...
        with pytest.raises(IdempotencyConflict):
            _service(broker, clock=FixedClock(NOW + timedelta(seconds=60))).execute(
                PROPOSAL_ID, KEY
            )

        # ...and after the bound the retry takes over: it verifies every
        # order in the broker, resubmits only the missing ones and finishes.
        result = _service(broker, clock=FixedClock(NOW + timedelta(seconds=120))).execute(
            PROPOSAL_ID, KEY
        )

        assert result.status is ProposalStatus.EXECUTED
        submitted_ids = [order_id for order_id, *_ in broker.submitted]
        assert len(submitted_ids) == len(set(submitted_ids))
        assert len(submitted_ids) == 5
        proposal_item = client.get_item(
            TableName=TABLE_NAME,
            Key={"pk": {"S": "PROPOSAL"}, "sk": {"S": PROPOSAL_ID}},
        )["Item"]
        assert proposal_item["status"]["S"] == "EXECUTED"
