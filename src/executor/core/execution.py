from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from common.clock import Clock
from common.config import ExecutorConfig
from common.errors import (
    IdempotencyConflict,
    MarketClosed,
    NotFoundError,
    PortfolioError,
    PriceDriftExceeded,
    ProposalExpired,
    ProposalNotPending,
)
from common.logging import get_logger
from executor.core.broker import BrokerPort
from executor.core.drift import check_price_drift
from executor.core.models import (
    ExecutionResult,
    FilledOrder,
    OrderPlan,
    OrderSnapshot,
    OrderStatus,
    PlannedOrder,
    ProposalRecord,
    ProposalStatus,
    Reconciliation,
)
from executor.core.planning import plan_orders
from executor.core.prices import ProposalPrices
from executor.core.state import ExecutionState

logger = get_logger(__name__)

_WEIGHT_SUM_TOLERANCE = 1e-6
_TERMINAL_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.REJECTED})


class RebalanceService:
    """Executes one proposal idempotently against a broker.

    Preconditions: the proposal exists in the state table in PENDING status,
    the broker is a paper trading interface and prices come from the gold
    layer.
    Postconditions: the idempotency key records exactly one final result, the
    proposal ends in EXECUTED/PARTIAL/FAILED and the reconciled state is
    written from real broker positions.
    """

    def __init__(
        self,
        state: ExecutionState,
        broker: BrokerPort,
        prices: ProposalPrices,
        config: ExecutorConfig,
        clock: Clock,
        waiter: Callable[[float], None] | None = None,
    ) -> None:
        self._state = state
        self._broker = broker
        self._prices = prices
        self._config = config
        self._clock = clock
        self._waiter = waiter if waiter is not None else time.sleep

    def execute(self, proposal_id: str, idempotency_key: str) -> ExecutionResult:
        """Run the full rebalance flow for one proposal and idempotency key."""
        claim = self._state.claim_idempotency(idempotency_key, proposal_id)
        if claim.result is not None:
            logger.info(
                "idempotent replay",
                extra={"proposal_id": proposal_id, "idempotency_key": idempotency_key},
            )
            return claim.result
        if not claim.fresh:
            raise IdempotencyConflict(
                f"execution for idempotency key {idempotency_key} is already in flight"
            )
        transitioned = False
        try:
            record = self._load_proposal(proposal_id)
            self._require_not_expired(record)
            self._require_pending(record)
            self._require_market_open(proposal_id)
            current_prices = self._current_prices(record)
            self._require_stable_prices(record, current_prices)
            self._state.transition_to_executing(proposal_id)
            transitioned = True
            plan = plan_orders(
                proposal_id,
                record.weights,
                self._broker.get_positions(),
                self._broker.get_account_equity(),
                current_prices,
                self._config.min_order_notional,
            )
            result = self._submit(plan, current_prices)
        except IdempotencyConflict:
            # the in-flight owner of the claim records its own result
            raise
        except PortfolioError as exc:
            self._record_failure(proposal_id, idempotency_key, exc, transitioned)
            raise
        self._persist(proposal_id, idempotency_key, result)
        return result

    def _load_proposal(self, proposal_id: str) -> ProposalRecord:
        record = self._state.read_proposal(proposal_id)
        if record is None:
            raise NotFoundError(f"proposal {proposal_id} not found")
        return record

    def _require_pending(self, record: ProposalRecord) -> None:
        if record.status != ProposalStatus.PENDING.value:
            raise ProposalNotPending(
                f"proposal {record.proposal_id} is {record.status}, expected PENDING"
            )

    def _require_not_expired(self, record: ProposalRecord) -> None:
        if self._clock.now() >= record.expires_at:
            raise ProposalExpired(
                f"proposal {record.proposal_id} expired at {record.expires_at.isoformat()}"
            )

    def _require_market_open(self, proposal_id: str) -> None:
        if not self._broker.is_market_open():
            logger.warning("market closed, execution rejected", extra={"proposal_id": proposal_id})
            raise MarketClosed("the market is closed")

    def _require_stable_prices(
        self,
        record: ProposalRecord,
        current_prices: Mapping[str, float],
    ) -> None:
        reference = self._prices.load(record.run_date)
        try:
            check_price_drift(reference, current_prices, self._config.max_price_drift_pct)
        except PriceDriftExceeded:
            logger.warning(
                "price drift exceeded",
                extra={"proposal_id": record.proposal_id},
            )
            raise

    def _current_prices(self, record: ProposalRecord) -> dict[str, float]:
        symbols = set(record.weights) | {p.symbol for p in self._broker.get_positions()}
        prices: dict[str, float] = {}
        for symbol in sorted(symbols):
            prices[symbol] = self._broker.get_price(symbol)
        return prices

    def _submit(self, plan: OrderPlan, current_prices: Mapping[str, float]) -> ExecutionResult:
        deadline = self._clock.now() + timedelta(seconds=self._config.execution_timeout_seconds)
        fills: list[FilledOrder] = []
        for order in plan.sales + plan.buys:
            logger.info(
                "order submitted",
                extra={
                    "proposal_id": plan.proposal_id,
                    "client_order_id": order.client_order_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": order.quantity,
                },
            )
            snapshot = self._broker.submit_order(
                order.client_order_id,
                order.symbol,
                order.side,
                order.quantity,
            )
            snapshot = self._await_fill(order, snapshot, deadline)
            fills.append(
                FilledOrder(
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    requested_quantity=order.quantity,
                    filled_quantity=snapshot.filled_quantity,
                    status=snapshot.status,
                )
            )
        status = ProposalStatus.EXECUTED if not fills else _classify(fills)
        reconciliation = self._reconcile(current_prices)
        if status is ProposalStatus.FAILED:
            return ExecutionResult(
                proposal_id=plan.proposal_id,
                status=ProposalStatus.FAILED,
                orders=fills,
                error="no order filled",
            )
        return ExecutionResult(
            proposal_id=plan.proposal_id,
            status=status,
            orders=fills,
            reconciliation=reconciliation,
        )

    def _await_fill(
        self,
        order: PlannedOrder,
        snapshot: OrderSnapshot,
        deadline: datetime,
    ) -> OrderSnapshot:
        while snapshot.status not in _TERMINAL_STATUSES:
            if self._clock.now() >= deadline:
                logger.warning(
                    "execution timeout reached",
                    extra={"client_order_id": order.client_order_id},
                )
                return snapshot
            self._waiter(0.1)
            snapshot = self._broker.get_order_status(order.client_order_id)
        return snapshot

    def _reconcile(self, current_prices: Mapping[str, float]) -> Reconciliation | None:
        positions = self._broker.get_positions()
        equity = self._broker.get_account_equity()
        values = {
            position.symbol: position.quantity * current_prices[position.symbol]
            for position in positions
        }
        total = sum(values.values())
        if total <= 0.0:
            return None
        weights = {symbol: value / total for symbol, value in values.items()}
        return Reconciliation(
            weights=weights,
            account_value=equity,
            reconciled_at=self._clock.now(),
        )

    def _persist(
        self,
        proposal_id: str,
        idempotency_key: str,
        result: ExecutionResult,
    ) -> None:
        now = self._clock.now()
        self._state.write_execution_result(proposal_id, result, now)
        if result.reconciliation is not None:
            self._state.write_reconciled_state(
                result.reconciliation.weights,
                result.reconciliation.account_value,
                now,
            )
        self._state.write_idempotency_result(idempotency_key, result, now)

    def _record_failure(
        self,
        proposal_id: str,
        idempotency_key: str,
        exc: PortfolioError,
        transitioned: bool,
    ) -> None:
        now = self._clock.now()
        if transitioned:
            failed = ExecutionResult(
                proposal_id=proposal_id,
                status=ProposalStatus.FAILED,
                orders=[],
                error=str(exc),
            )
            self._state.write_execution_result(proposal_id, failed, now)
        claim_failed = ExecutionResult(
            proposal_id=proposal_id,
            status=ProposalStatus.FAILED,
            orders=[],
            error=str(exc),
        )
        self._state.write_idempotency_result(idempotency_key, claim_failed, now)
        logger.error(
            "execution failed",
            extra={"proposal_id": proposal_id, "reason": str(exc)},
        )


def _classify(fills: list[FilledOrder]) -> ProposalStatus:
    if fills and all(fill.status is OrderStatus.FILLED for fill in fills):
        return ProposalStatus.EXECUTED
    if any(
        fill.status is OrderStatus.FILLED or fill.status is OrderStatus.PARTIALLY_FILLED
        for fill in fills
    ):
        return ProposalStatus.PARTIAL
    return ProposalStatus.FAILED
