from __future__ import annotations

import math
from collections.abc import Mapping

from executor.core.models import BrokerPosition, OrderPlan, OrderSide, PlannedOrder

_DUST_REASON = "dust"
_NO_PRICE_REASON = "no_price"


def plan_orders(
    proposal_id: str,
    target_weights: Mapping[str, float],
    positions: list[BrokerPosition],
    equity: float,
    prices: Mapping[str, float],
    min_order_notional: float,
) -> OrderPlan:
    """Build the deterministic order plan from real broker positions.

    Preconditions: ``target_weights`` sums to one, ``equity`` and every price
    are finite and positive, every symbol in ``target_weights`` or holding a
    position has a price.
    Postconditions: sells precede buys, quantities are positive, order ids are
    ``{proposal_id}-{symbol}-{side}`` and unique; deltas below
    ``min_order_notional`` are recorded in ``skipped`` and never ordered.
    """
    if equity <= 0.0 or not math.isfinite(equity):
        raise ValueError("equity must be finite and positive")
    if min_order_notional <= 0.0 or not math.isfinite(min_order_notional):
        raise ValueError("min_order_notional must be finite and positive")
    symbols = sorted(set(target_weights) | {position.symbol for position in positions})
    missing_prices = [symbol for symbol in symbols if symbol not in prices]
    if missing_prices:
        raise ValueError(f"missing prices for symbols: {', '.join(missing_prices)}")

    sales: list[PlannedOrder] = []
    buys: list[PlannedOrder] = []
    skipped: dict[str, str] = {}
    for symbol in symbols:
        price = prices[symbol]
        if price <= 0.0 or not math.isfinite(price):
            raise ValueError(f"price for {symbol} must be finite and positive")
        quantity = next(
            (position.quantity for position in positions if position.symbol == symbol),
            0.0,
        )
        delta_value = target_weights.get(symbol, 0.0) * equity - quantity * price
        if abs(delta_value) < min_order_notional:
            skipped[symbol] = _DUST_REASON
            continue
        side = OrderSide.BUY if delta_value > 0.0 else OrderSide.SELL
        order = PlannedOrder(
            symbol=symbol,
            side=side,
            quantity=abs(delta_value) / price,
            notional=abs(delta_value),
            client_order_id=f"{proposal_id}-{symbol}-{side.value}",
        )
        if side is OrderSide.SELL:
            sales.append(order)
        else:
            buys.append(order)
    return OrderPlan(
        proposal_id=proposal_id,
        sales=sales,
        buys=buys,
        skipped=skipped,
    )
