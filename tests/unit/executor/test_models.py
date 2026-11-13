from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from executor.core.models import (
    BrokerPosition,
    ExecutionResult,
    FilledOrder,
    OrderPlan,
    OrderSnapshot,
    PlannedOrder,
    ProposalRecord,
    ProposalStatus,
    Reconciliation,
    decimal_string,
)

NOW = datetime(2026, 11, 13, 21, 0, 0, tzinfo=UTC)


def _plan(*orders: PlannedOrder) -> OrderPlan:
    sales = [order for order in orders if order.side.value == "sell"]
    buys = [order for order in orders if order.side.value == "buy"]
    return OrderPlan(proposal_id="p", sales=sales, buys=buys, skipped={})


class TestBrokerPositionValidation:
    def test_rejects_an_empty_symbol(self) -> None:
        with pytest.raises(ValidationError, match="symbol must not be empty"):
            BrokerPosition(symbol="  ", quantity=1.0, price=1.0)

    @pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
    def test_rejects_invalid_quantities_and_prices(self, value: float) -> None:
        with pytest.raises(ValidationError):
            BrokerPosition(symbol="SPY", quantity=value, price=value)


class TestPlannedOrderValidation:
    def test_rejects_an_empty_symbol(self) -> None:
        with pytest.raises(ValidationError, match="symbol must not be empty"):
            PlannedOrder(
                symbol=" ",
                side="buy",
                quantity=1.0,
                notional=1.0,
                client_order_id="id",
            )

    @pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_invalid_quantities_and_notionals(self, value: float) -> None:
        with pytest.raises(ValidationError):
            PlannedOrder(
                symbol="SPY",
                side="buy",
                quantity=value,
                notional=value,
                client_order_id="id",
            )


class TestOrderPlanValidation:
    def test_rejects_duplicated_client_order_ids(self) -> None:
        first = PlannedOrder(
            symbol="SPY",
            side="buy",
            quantity=1.0,
            notional=10.0,
            client_order_id="dup",
        )
        second = PlannedOrder(
            symbol="QQQ",
            side="buy",
            quantity=1.0,
            notional=10.0,
            client_order_id="dup",
        )
        with pytest.raises(ValidationError, match="must be unique"):
            _plan(first, second)


class TestFilledOrderValidation:
    @pytest.mark.parametrize("value", [-0.5, float("nan"), float("inf")])
    def test_rejects_invalid_quantities(self, value: float) -> None:
        with pytest.raises(ValidationError):
            FilledOrder(
                client_order_id="id",
                symbol="SPY",
                side="buy",
                requested_quantity=10.0,
                filled_quantity=value,
                status="filled",
            )


class TestOrderSnapshotValidation:
    @pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
    def test_rejects_invalid_filled_quantity(self, value: float) -> None:
        with pytest.raises(ValidationError):
            OrderSnapshot(status="filled", filled_quantity=value)


class TestReconciliationValidation:
    def test_rejects_a_non_positive_account_value(self) -> None:
        with pytest.raises(ValidationError, match="account value"):
            Reconciliation(weights={"SPY": 1.0}, account_value=0.0, reconciled_at=NOW)

    def test_rejects_empty_weights(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            Reconciliation(weights={}, account_value=100.0, reconciled_at=NOW)

    def test_rejects_invalid_weights(self) -> None:
        with pytest.raises(ValidationError, match="finite and non-negative"):
            Reconciliation(
                weights={"SPY": -0.1, "QQQ": 1.1},
                account_value=100.0,
                reconciled_at=NOW,
            )

    def test_rejects_weights_that_do_not_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match="sum to one"):
            Reconciliation(
                weights={"SPY": 0.5, "QQQ": 0.6},
                account_value=100.0,
                reconciled_at=NOW,
            )


class TestExecutionResultValidation:
    def test_failed_results_require_an_error_message(self) -> None:
        with pytest.raises(ValidationError, match="error message"):
            ExecutionResult(
                proposal_id="p",
                status=ProposalStatus.FAILED,
                orders=[],
            )

    def test_non_failed_results_reject_an_error_message(self) -> None:
        with pytest.raises(ValidationError, match="only failed"):
            ExecutionResult(
                proposal_id="p",
                status=ProposalStatus.EXECUTED,
                orders=[],
                error="boom",
            )


class TestProposalRecordValidation:
    def _record(self, **overrides: object) -> ProposalRecord:
        values: dict[str, object] = {
            "proposal_id": "p",
            "status": "PENDING",
            "weights": {"SPY": 0.6, "QQQ": 0.4},
            "prices": {"SPY": 100.0},
            "created_at": NOW,
            "expires_at": NOW,
            "run_date": NOW.date(),
        }
        values.update(overrides)
        return ProposalRecord.model_validate(values)

    def test_rejects_empty_weights(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            self._record(weights={})

    def test_rejects_non_finite_weights(self) -> None:
        with pytest.raises(ValidationError, match="finite"):
            self._record(weights={"SPY": float("nan"), "QQQ": 1.0})

    def test_rejects_weights_that_do_not_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match="sum to one"):
            self._record(weights={"SPY": 0.6, "QQQ": 0.5})

    def test_rejects_invalid_prices(self) -> None:
        with pytest.raises(ValidationError, match="price"):
            self._record(prices={"SPY": -1.0})


class TestDecimalString:
    @pytest.mark.parametrize("value", [float("nan"), float("inf")])
    def test_rejects_non_finite_values(self, value: float) -> None:
        with pytest.raises(ValueError, match="finite"):
            decimal_string(value)
