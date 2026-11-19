from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

from alpaca.common.enums import BaseURL
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import OrderStatus as AlpacaOrderStatus
from alpaca.trading.enums import OrderType, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from common.errors import BrokerError, ConfigurationError
from common.logging import get_logger
from common.models import BrokerPosition, OrderSide, OrderSnapshot, OrderStatus

logger = get_logger(__name__)

_PAPER_TRADING_URL = BaseURL.TRADING_PAPER.value

# Alpaca order statuses that end the lifecycle without a fill. They map to
# REJECTED so the executor treats them as terminal and never waits on them.
# REPLACED is deliberately NOT here (N-26): a replaced order may still be
# alive under another id, so it maps to UNKNOWN and polling continues until
# the reconciliation reads the real positions.
_REJECTED_STATUSES = frozenset(
    {
        AlpacaOrderStatus.REJECTED,
        AlpacaOrderStatus.CANCELED,
        AlpacaOrderStatus.EXPIRED,
        AlpacaOrderStatus.DONE_FOR_DAY,
        AlpacaOrderStatus.STOPPED,
        AlpacaOrderStatus.SUSPENDED,
        AlpacaOrderStatus.CALCULATED,
    }
)

# Alpaca order statuses that are still open (polling continues). UNKNOWN is
# the executor's non-terminal marker.
_OPEN_STATUSES = frozenset(
    {
        AlpacaOrderStatus.NEW,
        AlpacaOrderStatus.PENDING_NEW,
        AlpacaOrderStatus.ACCEPTED,
        AlpacaOrderStatus.ACCEPTED_FOR_BIDDING,
        AlpacaOrderStatus.PENDING_CANCEL,
        AlpacaOrderStatus.PENDING_REPLACE,
        AlpacaOrderStatus.HELD,
        AlpacaOrderStatus.PENDING_REVIEW,
        AlpacaOrderStatus.REPLACED,
    }
)


def _require_paper_url(client: Any) -> None:
    """Fail closed unless the trading client provably targets the paper API.

    Precondition: the client was built by this module or a test double.
    Postcondition: raises ConfigurationError when the endpoint cannot be
    verified as the Alpaca paper trading URL (AGENTS.md section 6.6).
    """
    base_url = getattr(client, "_base_url", None)
    if base_url is None:
        raise ConfigurationError("cannot verify the trading endpoint (missing base url)")
    endpoint = base_url.value if isinstance(base_url, BaseURL) else str(base_url)
    if endpoint.rstrip("/") != _PAPER_TRADING_URL:
        raise ConfigurationError(
            "orders may only target the paper trading endpoint "
            f"({_PAPER_TRADING_URL}); got {endpoint}"
        )


class AlpacaPaperBroker:
    """Alpaca paper trading adapter implementing the BrokerPort.

    Preconditions: paper trading credentials (the constructor refuses live
    configuration), symbols are Alpaca equities.
    Postconditions: every order carries the caller's deterministic
    ``client_order_id``; no method silently swallows broker errors.

    The price source is the latest IEX trade, which on the free plan is
    delayed ~15 minutes and does not equal the consolidated tape (AGENTS.md
    section 7.4).
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool,
        trading_client: Any | None = None,
        data_client: Any | None = None,
        data_feed: DataFeed = DataFeed.IEX,
    ) -> None:
        if not paper:
            raise ConfigurationError("orders may only target the paper trading api")
        self._trading: Any = (
            trading_client
            if trading_client is not None
            else TradingClient(
                api_key=api_key,
                secret_key=secret_key,
                paper=True,
            )
        )
        _require_paper_url(self._trading)
        self._data: Any = (
            data_client
            if data_client is not None
            else StockHistoricalDataClient(
                api_key=api_key,
                secret_key=secret_key,
            )
        )
        self._feed = data_feed

    def is_market_open(self) -> bool:
        """Report the Alpaca market clock."""
        try:
            clock = self._trading.get_clock()
        except APIError as exc:
            raise BrokerError("alpaca market clock request failed") from exc
        return bool(clock.is_open)

    def get_positions(self) -> list[BrokerPosition]:
        """Return the current broker positions with no short-side entries."""
        try:
            raw_positions = self._trading.get_all_positions()
        except APIError as exc:
            raise BrokerError("alpaca positions request failed") from exc
        positions: list[BrokerPosition] = []
        for raw in raw_positions:
            symbol = str(raw.symbol).strip().upper()
            # Alpaca can report null qty/current_price for freshly opened or
            # unquoted positions; float(None) would raise an unclassified
            # TypeError, so the failure is mapped to BrokerError (N-14).
            if raw.qty is None or raw.current_price is None:
                raise BrokerError(f"alpaca reported a null quantity or price for {symbol}")
            quantity = float(raw.qty)
            if not math.isfinite(quantity) or quantity < 0.0:
                raise BrokerError(f"alpaca reported an unusable quantity for {symbol}: {quantity}")
            price = float(raw.current_price)
            if not math.isfinite(price) or price < 0.0:
                raise BrokerError(f"alpaca reported an unusable price for {symbol}: {price}")
            positions.append(BrokerPosition(symbol=symbol, quantity=quantity, price=price))
        return positions

    def get_account_equity(self) -> float:
        """Return the current paper account equity."""
        try:
            account = self._trading.get_account()
        except APIError as exc:
            raise BrokerError("alpaca account request failed") from exc
        equity = float(account.equity)
        if not math.isfinite(equity) or equity < 0.0:
            raise BrokerError(f"alpaca reported an unusable equity value: {equity}")
        return equity

    def get_price(self, symbol: str) -> float:
        """Return the latest IEX trade price for one symbol."""
        request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._feed)
        try:
            trades = self._data.get_stock_latest_trade(request)
        except APIError as exc:
            raise BrokerError(f"latest trade request failed for {symbol}") from exc
        trade = trades.get(symbol) if isinstance(trades, dict) else None
        if trade is None:
            raise BrokerError(f"alpaca returned no latest trade for {symbol}")
        price = float(trade.price)
        if not math.isfinite(price) or price <= 0.0:
            raise BrokerError(f"alpaca returned an unusable price for {symbol}: {price}")
        return price

    def get_adjusted_close(self, symbol: str, as_of: date) -> float:
        """Return the split/dividend-adjusted daily close for ``as_of``.

        The adjusted basis is the one in effect today, so the result is
        directly comparable with the live latest trade (N-15): corporate
        actions between the proposal date and now rebalance the historical
        series instead of distorting the executor's drift check.
        """
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(amount=1, unit=TimeFrameUnit.Day),
            adjustment=Adjustment.ALL,
            start=datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC),
            end=datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC) + timedelta(days=1),
            feed=self._feed,
        )
        try:
            bars = self._data.get_stock_bars(request)
        except APIError as exc:
            raise BrokerError(f"adjusted close request failed for {symbol}") from exc
        # alpaca-py 0.45 returns a BarSet whose ``data`` maps each symbol to a
        # list of Bar objects (``timestamp``, not ``date``); a missing symbol is
        # an empty series, not an error at this layer.
        series = list(getattr(bars, "data", {}).get(symbol, []))
        close = next(
            (float(bar.close) for bar in reversed(series) if bar.timestamp.date() == as_of),
            None,
        )
        if close is None:
            raise BrokerError(
                f"alpaca returned no adjusted close for {symbol} on {as_of.isoformat()}"
            )
        if not math.isfinite(close) or close <= 0.0:
            raise BrokerError(f"alpaca returned an unusable adjusted close for {symbol}: {close}")
        return close

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: float,
    ) -> OrderSnapshot:
        """Submit one market order restricted to regular trading hours."""
        request = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=AlpacaOrderSide.BUY if side is OrderSide.BUY else AlpacaOrderSide.SELL,
            type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            extended_hours=False,
            client_order_id=client_order_id,
        )
        try:
            order = self._trading.submit_order(request)
        except APIError as exc:
            raise BrokerError(f"alpaca rejected order {client_order_id}") from exc
        return _snapshot(order, client_order_id)

    def get_order_status(self, client_order_id: str) -> OrderSnapshot:
        """Look up one order by its deterministic client order id."""
        try:
            order = self._trading.get_order_by_client_id(client_order_id)
        except APIError as exc:
            if _is_not_found(exc):
                # 404: the broker holds no order with this client id. Report
                # NOT_FOUND (not UNKNOWN) so a retry may safely resubmit,
                # while a live order still keeps polling until the
                # reconciliation reads the real positions (AGENTS.md 6.15).
                logger.warning(
                    "order not found by client_order_id",
                    extra={"client_order_id": client_order_id},
                )
                return OrderSnapshot(status=OrderStatus.NOT_FOUND, filled_quantity=0.0)
            raise BrokerError(f"alpaca order lookup failed for {client_order_id}") from exc
        return _snapshot(order, client_order_id)


def _is_not_found(exc: APIError) -> bool:
    """Best-effort 404 detection across alpaca-py error shapes (N-26).

    ``APIError.status_code`` is only populated when the library attaches an
    ``HTTPError``; older or mocked errors may carry the code as a plain int
    or as the JSON ``code`` field.
    """
    status: int | None = exc.status_code
    if status is None:
        http_error = getattr(exc, "_http_error", None)
        if isinstance(http_error, int):
            status = http_error
        else:
            payload = getattr(exc, "_error", None)
            if isinstance(payload, dict) and isinstance(payload.get("code"), int):
                status = int(payload["code"])
            else:
                try:
                    candidate = exc.code
                    status = int(candidate) if candidate is not None else None
                except (KeyError, TypeError, ValueError):
                    # exc.code json-parses the raw error payload; a non-json
                    # message (TypeError) or an object without "code"
                    # (KeyError) carries no http status to match.
                    status = None
    return status == 404


def _snapshot(order: Any, client_order_id: str) -> OrderSnapshot:
    """Map an alpaca order payload onto the executor's snapshot model."""
    status = order.status
    if status in _REJECTED_STATUSES:
        mapped = OrderStatus.REJECTED
    elif status in _OPEN_STATUSES:
        mapped = OrderStatus.UNKNOWN
    elif status is AlpacaOrderStatus.PARTIALLY_FILLED:
        mapped = OrderStatus.PARTIALLY_FILLED
    elif status is AlpacaOrderStatus.FILLED:
        mapped = OrderStatus.FILLED
    else:
        raise BrokerError(f"alpaca reported an unmapped status {status} for {client_order_id}")
    filled = float(order.filled_qty)
    if not math.isfinite(filled) or filled < 0.0:
        raise BrokerError(f"alpaca reported an unusable fill for {client_order_id}: {filled}")
    return OrderSnapshot(status=mapped, filled_quantity=filled)
