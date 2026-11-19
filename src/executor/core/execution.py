from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Protocol

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
from common.models import OrderSnapshot, OrderStatus
from executor.core.broker import BrokerPort
from executor.core.drift import check_price_drift
from executor.core.models import (
    ExecutionResult,
    FilledOrder,
    OrderPlan,
    PlannedOrder,
    ProposalRecord,
    ProposalStatus,
    Reconciliation,
)
from executor.core.planning import plan_orders
from executor.core.state import ExecutionState

logger = get_logger(__name__)

_TERMINAL_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.REJECTED})
# Fill polling backs off from 0.5 s to 2 s. The broker's rate limit is ~200
# calls/min on the free plan (AGENTS.md section 7.4); a fixed 0.1 s interval
# polled every order at ~10 req/s and could exhaust it (mistakes.md QA-010).
_POLL_INITIAL_SECONDS = 0.5
_POLL_MAX_SECONDS = 2.0


class FailureNotifier(Protocol):
    """Publishes a failed or rejected execution attempt (AGENTS.md section 6.13)."""

    def notify_failure(self, proposal_id: str, status: ProposalStatus, reason: str) -> None: ...


class RebalanceService:
    """Executes one proposal idempotently against a broker.

    Preconditions: the proposal exists in the state table in PENDING status,
    the broker is a paper trading interface and prices come from the gold
    layer.
    Postconditions: the idempotency key records exactly one final result, the
    proposal ends in EXECUTED/PARTIAL/FAILED and the reconciled state is
    written from real broker positions. Every rejected or failed attempt is
    announced through the notifier, when one is configured.
    """

    def __init__(
        self,
        state: ExecutionState,
        broker: BrokerPort,
        config: ExecutorConfig,
        clock: Clock,
        waiter: Callable[[float], None] | None = None,
        notifier: FailureNotifier | None = None,
    ) -> None:
        self._state = state
        self._broker = broker
        self._config = config
        self._clock = clock
        self._waiter = waiter if waiter is not None else time.sleep
        self._notifier = notifier

    def execute(self, proposal_id: str, idempotency_key: str) -> ExecutionResult:
        """Run the full rebalance flow for one proposal and idempotency key."""
        claim = self._state.claim_idempotency(idempotency_key, proposal_id, self._clock.now())
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
            result = self._submit(plan)
        except IdempotencyConflict:
            # the in-flight owner of the claim records its own result
            raise
        except PortfolioError as exc:
            self._record_failure(proposal_id, idempotency_key, exc, transitioned)
            # A failure after the EXECUTING transition leaves the proposal
            # FAILED; a rejection before it leaves the proposal PENDING and
            # retryable. Both are announced so no outcome is silent.
            self._announce_failure(
                proposal_id,
                ProposalStatus.FAILED if transitioned else ProposalStatus.PENDING,
                str(exc),
            )
            raise
        self._persist(proposal_id, idempotency_key, result)
        return result

    def _announce_failure(self, proposal_id: str, status: ProposalStatus, reason: str) -> None:
        """Notify a failed attempt; a notification error never masks the failure."""
        if self._notifier is None:
            return
        try:
            self._notifier.notify_failure(proposal_id, status, reason)
        except PortfolioError:
            logger.exception(
                "failure notification failed",
                extra={"proposal_id": proposal_id, "status": status.value},
            )

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
        # N-15: the drift reference is the adjusted close for the proposal's
        # run date re-fetched at execution time, so both sides share the
        # adjustment basis in effect today. Comparing against the prices
        # stored at proposal time would misread splits and dividend
        # rebasings as drift and reject valid proposals.
        reference = {
            symbol: self._broker.get_adjusted_close(symbol, record.run_date)
            for symbol in sorted(current_prices)
        }
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

    def _submit(self, plan: OrderPlan) -> ExecutionResult:
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
        # Re-price after the fills: the reconciled weights must reflect the
        # market at reconciliation time, not the prices used to plan the
        # orders (mistakes.md QA-015).
        reconciliation = self._reconcile()
        if status is ProposalStatus.FAILED:
            # Even when every order was rejected the positions and equity are
            # the real broker state, so the failure still reconciles it.
            return ExecutionResult(
                proposal_id=plan.proposal_id,
                status=ProposalStatus.FAILED,
                orders=fills,
                error="no order filled",
                reconciliation=reconciliation,
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
        interval = _POLL_INITIAL_SECONDS
        while snapshot.status not in _TERMINAL_STATUSES:
            remaining = (deadline - self._clock.now()).total_seconds()
            if remaining <= 0.0:
                logger.warning(
                    "execution timeout reached",
                    extra={"client_order_id": order.client_order_id},
                )
                return snapshot
            self._waiter(min(interval, remaining))
            interval = min(interval * 2.0, _POLL_MAX_SECONDS)
            snapshot = self._broker.get_order_status(order.client_order_id)
        return snapshot

    def _reconcile(self) -> Reconciliation | None:
        """Value the real broker positions at current market prices.

        Postconditions: returns ``None`` when there is nothing to reconcile
        (no positions or non-positive equity); otherwise the weights are the
        position values divided by the account equity.
        """
        positions = self._broker.get_positions()
        equity = self._broker.get_account_equity()
        if not positions or equity <= 0.0:
            logger.warning(
                "reconciliation skipped: no positions or non-positive equity",
                extra={"positions": len(positions)},
            )
            return None
        # Weights are fractions of the account equity, so uninvested cash is
        # the remainder and sum(weights) <= 1. Dividing by the position total
        # would overstate every holding whenever cash exists.
        values = {
            position.symbol: position.quantity * self._broker.get_price(position.symbol)
            for position in positions
        }
        weights = {symbol: value / equity for symbol, value in values.items()}
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
            # Orders may have been submitted or filled before the failure
            # (AGENTS.md section 6.12): the persisted state must never stay
            # stale, so reconcile from real broker positions. Every write
            # here is best effort; the original failure keeps propagating.
            reconciliation = self._post_failure_reconciliation(proposal_id)
            if reconciliation is not None:
                try:
                    self._state.write_reconciled_state(
                        reconciliation.weights,
                        reconciliation.account_value,
                        now,
                    )
                except PortfolioError:
                    logger.exception(
                        "failed to persist the post-failure reconciliation",
                        extra={"proposal_id": proposal_id},
                    )
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

    def _post_failure_reconciliation(self, proposal_id: str) -> Reconciliation | None:
        """Best-effort reconciliation with whatever the broker holds now.

        Postconditions: returns the reconciliation from real positions, or
        ``None`` if the broker or the price feed also failed; never raises.
        """
        try:
            return self._reconcile()
        except PortfolioError:
            logger.exception(
                "post-failure reconciliation failed; orders in flight must be "
                "queried by client_order_id",
                extra={"proposal_id": proposal_id},
            )
            return None


def _classify(fills: list[FilledOrder]) -> ProposalStatus:
    if fills and all(fill.status is OrderStatus.FILLED for fill in fills):
        return ProposalStatus.EXECUTED
    if any(
        fill.status is OrderStatus.FILLED or fill.status is OrderStatus.PARTIALLY_FILLED
        for fill in fills
    ):
        return ProposalStatus.PARTIAL
    return ProposalStatus.FAILED
