from __future__ import annotations

from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.trading.enums import OrderStatus as AlpacaOrderStatus

from common.adapters.alpaca import AlpacaPaperBroker, _snapshot
from common.errors import BrokerError, ConfigurationError
from common.models import OrderSide, OrderStatus

_PAPER_URL = "https://paper-api.alpaca.markets"
_LIVE_URL = "https://api.alpaca.markets"


class _FakeTradingClient:
    def __init__(self, base_url: str = _PAPER_URL) -> None:
        self._base_url = base_url
        self.clock = SimpleNamespace(is_open=True)
        self.positions: list[SimpleNamespace] = []
        self.account = SimpleNamespace(equity="10000.00")
        self.orders: dict[str, SimpleNamespace] = {}
        self.submitted: list[SimpleNamespace] = []
        self.fail_on = set[str]()

    def get_clock(self) -> SimpleNamespace:
        if "clock" in self.fail_on:
            raise APIError("clock down", 503)
        return self.clock

    def get_all_positions(self) -> list[SimpleNamespace]:
        if "positions" in self.fail_on:
            raise APIError("positions down", 503)
        return self.positions

    def get_account(self) -> SimpleNamespace:
        if "account" in self.fail_on:
            raise APIError("account down", 503)
        return self.account

    def submit_order(self, request: SimpleNamespace) -> SimpleNamespace:
        self.submitted.append(request)
        return SimpleNamespace(status=AlpacaOrderStatus.FILLED, filled_qty=request.qty)

    def get_order_by_client_id(self, client_order_id: str) -> SimpleNamespace:
        if client_order_id not in self.orders:
            raise APIError("order not found", 404)
        return self.orders[client_order_id]


class _FakeDataClient:
    def __init__(self, trades: dict[str, float] | None = None) -> None:
        self.trades = trades if trades is not None else {"SPY": 500.25}
        self.requests: list[SimpleNamespace] = []

    def get_stock_latest_trade(self, request: SimpleNamespace) -> dict[str, SimpleNamespace]:
        self.requests.append(request)
        symbol = request.symbol_or_symbols
        if symbol not in self.trades:
            raise APIError("no trade", 404)
        return {symbol: SimpleNamespace(price=self.trades[symbol])}


def _broker(
    base_url: str = _PAPER_URL,
    trading: _FakeTradingClient | None = None,
    data: _FakeDataClient | None = None,
    paper: bool = True,
) -> AlpacaPaperBroker:
    return AlpacaPaperBroker(
        api_key="key",
        secret_key="secret",
        paper=paper,
        trading_client=trading if trading is not None else _FakeTradingClient(base_url),
        data_client=data if data is not None else _FakeDataClient(),
    )


def test_refuses_live_configuration() -> None:
    with pytest.raises(ConfigurationError):
        _broker(paper=False)


def test_refuses_unverifiable_trading_endpoint() -> None:
    with pytest.raises(ConfigurationError):
        AlpacaPaperBroker(
            api_key="key",
            secret_key="secret",
            paper=True,
            trading_client=SimpleNamespace(),
            data_client=_FakeDataClient(),
        )


def test_refuses_live_endpoint_even_with_paper_flag() -> None:
    with pytest.raises(ConfigurationError):
        _broker(base_url=_LIVE_URL)


def test_market_open_reads_the_clock() -> None:
    assert _broker().is_market_open() is True


def test_market_clock_failure_maps_to_broker_error() -> None:
    trading = _FakeTradingClient()
    trading.fail_on.add("clock")
    with pytest.raises(BrokerError):
        _broker(trading=trading).is_market_open()


def test_positions_map_symbol_quantity_price() -> None:
    trading = _FakeTradingClient()
    trading.positions = [
        SimpleNamespace(symbol=" spy ", qty="7", current_price="500.5"),
        SimpleNamespace(symbol="TLT", qty="3", current_price="90"),
    ]
    positions = _broker(trading=trading).get_positions()
    assert [(p.symbol, p.quantity, p.price) for p in positions] == [
        ("SPY", 7.0, 500.5),
        ("TLT", 3.0, 90.0),
    ]


def test_short_position_is_rejected_not_silently_accepted() -> None:
    trading = _FakeTradingClient()
    trading.positions = [SimpleNamespace(symbol="SPY", qty="-5", current_price="500")]
    with pytest.raises(BrokerError):
        _broker(trading=trading).get_positions()


def test_account_equity_is_a_positive_float() -> None:
    assert _broker().get_account_equity() == 10000.0


def test_price_uses_the_latest_trade() -> None:
    data = _FakeDataClient({"SPY": 501.5})
    assert _broker(data=data).get_price("SPY") == 501.5
    assert data.requests[0].symbol_or_symbols == "SPY"


def test_unknown_price_symbol_raises_broker_error() -> None:
    with pytest.raises(BrokerError):
        _broker().get_price("MISSING")


def test_zero_price_raises_broker_error() -> None:
    data = _FakeDataClient({"SPY": 0.0})
    with pytest.raises(BrokerError):
        _broker(data=data).get_price("SPY")


def test_submit_order_sends_a_market_day_order_with_the_deterministic_id() -> None:
    trading = _FakeTradingClient()
    snapshot = _broker(trading=trading).submit_order(
        "prop-SPY-sell",
        "SPY",
        OrderSide.SELL,
        2.0,
    )
    assert snapshot.status is OrderStatus.FILLED
    assert snapshot.filled_quantity == 2.0
    request = trading.submitted[0]
    assert request.client_order_id == "prop-SPY-sell"
    assert request.symbol == "SPY"
    assert str(request.side.value) == "sell"
    assert request.extended_hours is False


def test_get_order_status_maps_open_order_to_unknown() -> None:
    trading = _FakeTradingClient()
    trading.orders["id-1"] = SimpleNamespace(
        status=AlpacaOrderStatus.ACCEPTED,
        filled_qty="1",
    )
    snapshot = _broker(trading=trading).get_order_status("id-1")
    assert snapshot.status is OrderStatus.UNKNOWN
    assert snapshot.filled_quantity == 1.0


def test_get_order_status_maps_canceled_to_rejected() -> None:
    trading = _FakeTradingClient()
    trading.orders["id-1"] = SimpleNamespace(
        status=AlpacaOrderStatus.CANCELED,
        filled_qty="0",
    )
    snapshot = _broker(trading=trading).get_order_status("id-1")
    assert snapshot.status is OrderStatus.REJECTED


def test_unknown_order_id_raises_broker_error() -> None:
    with pytest.raises(BrokerError):
        _broker().get_order_status("missing-id")


def test_snapshot_rejects_an_unmapped_status() -> None:
    order = SimpleNamespace(status="not-a-status", filled_qty="0")
    with pytest.raises(BrokerError):
        _snapshot(order, "id")


def test_default_data_feed_is_iex() -> None:
    assert _broker()._feed is DataFeed.IEX
