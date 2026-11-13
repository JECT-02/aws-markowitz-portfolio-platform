from __future__ import annotations

from typing import Protocol

from executor.core.models import BrokerPosition, OrderSide, OrderStatus


class BrokerPort(Protocol):
    """Trading interface consumed by the executor.

    Preconditions: prices are finite and positive, quantities are non-negative.
    Postconditions: submission is idempotent per ``client_order_id``; status
    queries never mutate state.
    """

    def is_market_open(self) -> bool: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_account_equity(self) -> float: ...

    def get_price(self, symbol: str) -> float: ...

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderStatus: ...

    def get_order_status(self, client_order_id: str) -> OrderStatus: ...
