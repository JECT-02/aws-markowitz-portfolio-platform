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
        snapshot = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert snapshot.status is OrderStatus.FILLED
        assert snapshot.filled_quantity == pytest.approx(10.0)
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
        assert first == second
        assert len(broker.submitted) == 1

    def test_partial_fill_ratio_reports_partial_status(self) -> None:
        broker = SimulatedBroker(
            [],
            equity=1_000.0,
            prices={"AAA": 10.0},
            partial_fill_ratio={"AAA": 0.5},
        )
        snapshot = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert snapshot.status is OrderStatus.PARTIALLY_FILLED
        assert snapshot.filled_quantity == pytest.approx(5.0)
        assert broker.get_positions()[0].quantity == pytest.approx(5.0)

    def test_rejected_symbol_never_fills(self) -> None:
        broker = SimulatedBroker(
            [],
            equity=1_000.0,
            prices={"AAA": 10.0},
            reject_symbols={"AAA"},
        )
        snapshot = broker.submit_order("o1", "AAA", OrderSide.BUY, 10.0)
        assert snapshot.status is OrderStatus.REJECTED
        assert snapshot.filled_quantity == 0.0
        assert broker.get_positions() == []

    def test_unknown_order_status(self) -> None:
        broker = SimulatedBroker([], equity=1_000.0, prices={"AAA": 10.0})
        snapshot = broker.get_order_status("missing")
        assert snapshot.status is OrderStatus.UNKNOWN
        assert snapshot.filled_quantity == 0.0

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


class TestPlanOrdersGuards:
    def test_rejects_non_positive_equity(self) -> None:
        with pytest.raises(ValueError, match="equity"):
            plan_orders(
                "p1", {"AAA": 1.0}, [], equity=0.0, prices={"AAA": 10.0}, min_order_notional=10.0
            )

    def test_rejects_non_positive_min_order_notional(self) -> None:
        with pytest.raises(ValueError, match="min_order_notional"):
            plan_orders(
                "p1", {"AAA": 1.0}, [], equity=100.0, prices={"AAA": 10.0}, min_order_notional=0.0
            )

    def test_rejects_invalid_price_for_a_symbol(self) -> None:
        with pytest.raises(ValueError, match="AAA"):
            plan_orders(
                "p1",
                {"AAA": 1.0},
                [],
                equity=100.0,
                prices={"AAA": 0.0},
                min_order_notional=10.0,
            )


class TestSimulatedBrokerGuards:
    def test_rejects_non_positive_equity(self) -> None:
        with pytest.raises(ValueError, match="equity"):
            SimulatedBroker([], equity=0.0, prices={"AAA": 10.0})

    def test_rejects_non_positive_prices(self) -> None:
        with pytest.raises(ValueError, match="prices"):
            SimulatedBroker([], equity=100.0, prices={"AAA": 0.0})

    def test_set_market_open_toggles_submission(self) -> None:
        broker = SimulatedBroker([], equity=100.0, prices={"AAA": 10.0})
        broker.set_market_open(False)
        assert broker.is_market_open() is False
        with pytest.raises(RuntimeError, match="closed"):
            broker.submit_order("o1", "AAA", OrderSide.BUY, 5.0)
        broker.set_market_open(True)
        assert broker.is_market_open() is True

    def test_get_price_for_unknown_symbol_raises(self) -> None:
        broker = SimulatedBroker([], equity=100.0, prices={"AAA": 10.0})
        with pytest.raises(KeyError, match="BBB"):
            broker.get_price("bbb")

    def test_set_price_validates_and_normalizes(self) -> None:
        broker = SimulatedBroker([], equity=100.0, prices={"AAA": 10.0})
        with pytest.raises(ValueError, match="positive"):
            broker.set_price("AAA", 0.0)
        broker.set_price("aaa", 12.0)
        assert broker.get_price("AAA") == pytest.approx(12.0)

    def test_unknown_orders_report_unknown_without_filling(self) -> None:
        broker = SimulatedBroker(
            [],
            equity=100.0,
            prices={"AAA": 10.0},
            unknown_orders={"stale"},
        )
        snapshot = broker.submit_order("stale", "AAA", OrderSide.BUY, 5.0)
        assert snapshot.status is OrderStatus.UNKNOWN
        assert snapshot.filled_quantity == 0.0
        assert broker.submitted == []

    def test_now_reports_the_wall_clock(self) -> None:
        broker = SimulatedBroker([], equity=100.0, prices={"AAA": 10.0})
        assert broker.now().tzinfo is not None
