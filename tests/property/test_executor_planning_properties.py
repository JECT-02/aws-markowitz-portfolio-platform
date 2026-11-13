from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from executor.core.models import BrokerPosition, OrderSide
from executor.core.planning import plan_orders

SETTINGS = settings(max_examples=40, deadline=None)


def _weights(draw: st.DrawFn, symbols: list[str]) -> dict[str, float]:
    raw = [draw(st.floats(min_value=0.0, max_value=1.0)) for _ in symbols]
    total = sum(raw)
    if total == 0.0:
        raw[0] = 1.0
        total = 1.0
    return {symbol: value / total for symbol, value in zip(symbols, raw, strict=True)}


@st.composite
def _scenario(
    draw: st.DrawFn,
) -> tuple[dict[str, float], list[BrokerPosition], dict[str, float], float, float]:
    n = draw(st.integers(min_value=1, max_value=6))
    symbols = [f"S{index}" for index in range(n)]
    prices = {symbol: draw(st.floats(min_value=1.0, max_value=500.0)) for symbol in symbols}
    positions = [
        BrokerPosition(
            symbol=symbol,
            quantity=draw(st.floats(min_value=0.0, max_value=1_000.0)),
            price=prices[symbol],
        )
        for symbol in symbols
        if draw(st.booleans())
    ]
    equity = draw(st.floats(min_value=1_000.0, max_value=100_000.0))
    min_notional = draw(st.floats(min_value=0.5, max_value=200.0))
    return _weights(draw, symbols), positions, prices, equity, min_notional


@SETTINGS
@given(_scenario())
def test_planning_invariants(
    scenario: tuple[dict[str, float], list[BrokerPosition], dict[str, float], float, float],
) -> None:
    weights, positions, prices, equity, min_notional = scenario
    plan = plan_orders(
        "prop",
        weights,
        positions,
        equity,
        prices,
        min_notional,
    )
    orders = plan.sales + plan.buys
    identifiers = [order.client_order_id for order in orders]
    assert len(set(identifiers)) == len(identifiers)
    assert all(order.quantity > 0.0 for order in orders)
    assert all(order.notional >= min_notional for order in orders)
    assert all(math.isfinite(order.quantity) and math.isfinite(order.notional) for order in orders)
    assert all(
        order.client_order_id == f"prop-{order.symbol}-{order.side.value}" for order in orders
    )
    for order in orders:
        position = next((p for p in positions if p.symbol == order.symbol), None)
        current_value = position.quantity * prices[order.symbol] if position else 0.0
        delta = weights.get(order.symbol, 0.0) * equity - current_value
        assert abs(delta) >= min_notional
        if order.side is OrderSide.BUY:
            assert delta > 0.0
        else:
            assert delta < 0.0
        assert order.quantity == pytest.approx(abs(delta) / prices[order.symbol])
