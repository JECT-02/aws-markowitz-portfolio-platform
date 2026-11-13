from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from executor.core.models import BrokerPosition, OrderSide, OrderStatus


@dataclass(frozen=True)
class _SimulatedOrder:
    symbol: str
    side: OrderSide
    requested: float
    filled: float
    status: OrderStatus


class SimulatedBroker:
    """In-memory broker used for local runs and tests; never a production adapter.

    Fills are deterministic: every order fills immediately unless the caller
    configures a partial fill ratio, a rejection symbol or a stale order id.
    """

    def __init__(
        self,
        positions: list[BrokerPosition],
        equity: float,
        prices: dict[str, float],
        market_open: bool = True,
        partial_fill_ratio: dict[str, float] | None = None,
        reject_symbols: set[str] | None = None,
        unknown_orders: set[str] | None = None,
    ) -> None:
        self._positions: dict[str, BrokerPosition] = {
            position.symbol: position for position in positions
        }
        if equity <= 0.0:
            raise ValueError("equity must be positive")
        self._equity = equity
        if any(price <= 0.0 for price in prices.values()):
            raise ValueError("prices must be positive")
        self._prices = prices
        self._market_open = market_open
        self._partial_fill_ratio = partial_fill_ratio or {}
        self._reject_symbols = reject_symbols or set()
        self._unknown_orders = set(unknown_orders or ())
        self._orders: dict[str, _SimulatedOrder] = {}
        self.submitted: list[tuple[str, str, OrderSide, float]] = []

    def is_market_open(self) -> bool:
        return self._market_open

    def set_market_open(self, market_open: bool) -> None:
        self._market_open = market_open

    def get_positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    def get_account_equity(self) -> float:
        return self._equity

    def get_price(self, symbol: str) -> float:
        symbol = symbol.strip().upper()
        if symbol not in self._prices:
            raise KeyError(f"no simulated price for {symbol}")
        return self._prices[symbol]

    def set_price(self, symbol: str, price: float) -> None:
        if price <= 0.0:
            raise ValueError("price must be positive")
        self._prices[symbol.strip().upper()] = price

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderStatus:
        if not self._market_open:
            raise RuntimeError("market is closed")
        recorded = self._orders.get(client_order_id)
        if recorded is not None:
            return recorded.status
        if client_order_id in self._unknown_orders:
            return OrderStatus.UNKNOWN
        symbol = symbol.strip().upper()
        if symbol in self._reject_symbols:
            status = OrderStatus.REJECTED
            filled = 0.0
        else:
            ratio = self._partial_fill_ratio.get(symbol, 1.0)
            filled = quantity * ratio
            status = OrderStatus.FILLED if ratio >= 1.0 else OrderStatus.PARTIALLY_FILLED
        self._orders[client_order_id] = _SimulatedOrder(
            symbol=symbol,
            side=side,
            requested=quantity,
            filled=filled,
            status=status,
        )
        self.submitted.append((client_order_id, symbol, side, quantity))
        self._apply_fill(symbol, side, filled)
        return status

    def get_order_status(self, client_order_id: str) -> OrderStatus:
        order = self._orders.get(client_order_id)
        if order is None:
            return OrderStatus.UNKNOWN
        return order.status

    def _apply_fill(self, symbol: str, side: OrderSide, filled: float) -> None:
        current = self._positions.get(symbol)
        quantity = current.quantity if current is not None else 0.0
        price = self._prices[symbol]
        if side is OrderSide.BUY:
            quantity += filled
        else:
            quantity = max(0.0, quantity - filled)
        if quantity <= 0.0:
            self._positions.pop(symbol, None)
        else:
            self._positions[symbol] = BrokerPosition(symbol=symbol, quantity=quantity, price=price)

    def now(self) -> datetime:
        return datetime.now(UTC)
