from __future__ import annotations

import pytest

from executor.core.models import BrokerPosition, OrderSide, OrderStatus
from executor.core.planning import plan_orders
from executor.core.simulated import SimulatedBroker


def _positions(*entries: tuple[str, float, float]) -> list[BrokerPosition]:
    return [BrokerPosition(symbol=s, quantity=q, price=p) for s, q, p in entries]


class TestPlanOrders:
    def test_sells_precede_buys(self) -> None:
        plan = plan_orders(
            "p1",
            {"AAA": 0.5, "BBB": 0.5},
            _positions(("AAA", 3_000.0, 10.0)),
            equity=20_000.0,
            prices={"AAA": 10.0, "BBB": 10.0},
            min_order_notional=10.0,
        )
        assert [order.symbol for order in plan.sales] == ["AAA"]
        assert [order.symbol for order in plan.buys] == ["BBB"]
        assert all(order.side is OrderSide.SELL for order in plan.sales)
        assert all(order.side is OrderSide.BUY for order in plan.buys)

    def test_buy_quantity_is_positive_notional_over_price(self) -> None:
        plan = plan_orders(
            "p1",
            {"AAA": 1.0},
            _positions(("AAA", 10.0, 10.0)),
            equity=2_000.0,
            prices={"AAA": 10.0},
            min_order_notional=10.0,
        )
        order = plan.buys[0]
        assert order.quantity == pytest.approx(190.0)
        assert order.notional == pytest.approx(1_900.0)
        assert order.client_order_id == "p1-AAA-buy"

    def test_sell_all_position_when_target_is_zero(self) -> None:
        plan = plan_orders(
            "p1",
            {"AAA": 0.0, "BBB": 1.0},
            _positions(("AAA", 50.0, 10.0)),
            equity=1_000.0,
            prices={"AAA": 10.0, "BBB": 10.0},
            min_order_notional=10.0,
        )
        assert [order.symbol for order in plan.sales] == ["AAA"]
        assert plan.sales[0].quantity == pytest.approx(50.0)
        assert plan.sales[0].notional == pytest.approx(500.0)

    def test_dust_delta_is_skipped_and_never_ordered(self) -> None:
        plan = plan_orders(
            "p1",
            {"AAA": 0.5, "BBB": 0.5},
            _positions(("AAA", 100.0, 10.0), ("BBB", 100.0, 10.0)),
            equity=2_000.0,
            prices={"AAA": 10.0, "BBB": 10.0},
            min_order_notional=500.0,
        )
        assert not plan.sales and not plan.buys
        assert plan.skipped == {"AAA": "dust", "BBB": "dust"}

    def test_missing_price_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="missing prices"):
            plan_orders(
                "p1",
                {"AAA": 1.0},
                [],
                equity=1_000.0,
                prices={},
                min_order_notional=10.0,
            )

    def test_client_order_ids_are_unique(self) -> None:
        plan = plan_orders(
            "p1",
            {"AAA": 0.5, "BBB": 0.5},
            _positions(("AAA", 100.0, 10.0)),
            equity=20_000.0,
            prices={"AAA": 10.0, "BBB": 10.0},
            min_order_notional=10.0,
        )
        identifiers = [order.client_order_id for order in plan.sales + plan.buys]
        assert len(set(identifiers)) == len(identifiers)


class TestSimulatedBroker:
    def test_fills_fill_the_position(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0})
        status = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert status is OrderStatus.FILLED
        assert broker.get_positions()[0].quantity == pytest.approx(10.0)

    def test_sell_respects_zero_floor(self) -> None:
        broker = SimulatedBroker(
            _positions(("AAA", 5.0, 10.0)),
            equity=1_000.0,
            prices={"AAA": 10.0},
        )
        broker.submit_order("o1", "AAA", OrderSide.SELL, 10.0)
        assert broker.get_positions() == []

    def test_repeated_client_order_id_does_not_resubmit(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0})
        first = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        second = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert first is second
        assert len(broker.submitted) == 1

    def test_partial_fill_ratio_reports_partial_status(self) -> None:
        broker = SimulatedBroker(
            [],
            equity=1_000.0,
            prices={"AAA": 10.0},
            partial_fill_ratio={"AAA": 0.5},
        )
        status = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert status is OrderStatus.PARTIALLY_FILLED
        assert broker.get_positions()[0].quantity == pytest.approx(5.0)

    def test_rejected_symbol_never_fills(self) -> None:
        broker = SimulatedBroker(
            [],
            equity=1_000.0,
            prices={"AAA": 10.0},
            reject_symbols={"AAA"},
        )
        status = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert status is OrderStatus.REJECTED
        assert broker.get_positions() == []

    def test_unknown_order_status(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0})
        assert broker.get_order_status("missing") is OrderStatus.UNKNOWN

    def test_closed_market_rejects_submission(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0}, market_open=False)
        with pytest.raises(RuntimeError, match="closed"):
            broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)

    def test_get_price_normalizes_symbol(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0})
        assert broker.get_price(" aaa ") == pytest.approx(10.0)

    def test_equity_is_unchanged_by_fills(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0})
        broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert broker.get_account_equity() == pytest.approx(1_000.0)
