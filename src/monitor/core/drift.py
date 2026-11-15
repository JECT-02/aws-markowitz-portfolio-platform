"""Pure drift computation for the fast monitoring loop.

No I/O here: positions, the latest proposal weights and the account equity
enter as plain values, so the drift math is directly unit- and
property-testable (AGENTS.md section 3).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from common.models import BrokerPosition
from optimizer.core.models import Proposal

# The proposal weights must sum to one within the usual float noise.
_WEIGHT_SUM_TOLERANCE = 1e-6

# Live weights are derived from positions valued at IEX prices while the
# account equity uses the broker's own valuation; both lag the consolidated
# tape by ~15 minutes on the free plan (AGENTS.md section 7.4), so the live
# sum may drift from one by more than float noise before any trade happens.
# One percent absorbs that valuation lag; beyond it the data is unusable and
# the run must fail instead of storing a misleading snapshot.
_LIVE_WEIGHT_SUM_TOLERANCE = 0.01


class DriftSnapshot(BaseModel):
    """One fast-loop observation of the live portfolio against w*."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    run_date: date
    weights_live: dict[str, float]
    drift_l1: float
    rebalance_required: bool
    computed_at: datetime

    @field_validator("weights_live")
    @classmethod
    def _validate_weights(cls, value: dict[str, float]) -> dict[str, float]:
        for symbol, weight in value.items():
            if not symbol or symbol != symbol.strip().upper():
                raise ValueError(f"weight symbol {symbol!r} must be normalized")
            if not math.isfinite(weight) or weight < 0.0:
                raise ValueError(f"live weight for {symbol} must be finite and non-negative")
        if math.fsum(value.values()) > 1.0 + _LIVE_WEIGHT_SUM_TOLERANCE:
            raise ValueError("live weights must not exceed one beyond the valuation tolerance")
        return value

    @field_validator("drift_l1")
    @classmethod
    def _validate_drift(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("drift must be finite and non-negative")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> DriftSnapshot:
        # Triangle inequality: drift >= sum(w_live) - sum(w_star), with the
        # proposal weights summing to one within float noise. A stored drift
        # below that bound would be inconsistent with the stored weights.
        live_total = math.fsum(self.weights_live.values())
        if self.drift_l1 < live_total - 1.0 - (_WEIGHT_SUM_TOLERANCE + 1e-9):
            raise ValueError("drift_l1 is inconsistent with the stored live weights")
        return self


def live_weights(
    positions: list[BrokerPosition],
    account_equity: float,
) -> dict[str, float]:
    """Derive live portfolio weights from real broker positions.

    Preconditions: every position carries a non-negative quantity and price
    (validated by ``BrokerPosition``); ``account_equity`` is finite and
    positive. Postconditions: each weight equals position value divided by
    equity, the weights sum to at most one plus the valuation tolerance, and
    the implied cash share is simply absent from the mapping.
    """
    if not math.isfinite(account_equity) or account_equity <= 0.0:
        raise ValueError("account equity must be finite and positive")
    if not positions:
        return {}
    values = {position.symbol: position.quantity * position.price for position in positions}
    for symbol, value in values.items():
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"position value for {symbol} must be finite and non-negative")
    total = math.fsum(values.values())
    if total > account_equity * (1.0 + _LIVE_WEIGHT_SUM_TOLERANCE):
        raise ValueError("position values exceed the account equity beyond the valuation tolerance")
    return {symbol: value / account_equity for symbol, value in sorted(values.items())}


def drift_l1(
    weights_live: Mapping[str, float],
    weights_star: Mapping[str, float],
) -> float:
    """Compute ``||w_live - w*||_1`` over the union of both symbol sets.

    Preconditions: every weight is finite and non-negative. Postcondition:
    the result is finite and non-negative; symbols present in one mapping
    only count with their full weight against zero on the other side.
    """
    for name, weights in (("live", weights_live), ("star", weights_star)):
        for symbol, weight in weights.items():
            if not math.isfinite(weight) or weight < 0.0:
                raise ValueError(f"{name} weight for {symbol} must be finite and non-negative")
    symbols = set(weights_live) | set(weights_star)
    return math.fsum(
        abs(weights_live.get(symbol, 0.0) - weights_star.get(symbol, 0.0)) for symbol in symbols
    )


def drift_snapshot(
    proposal: Proposal,
    positions: list[BrokerPosition],
    account_equity: float,
    rebalance_threshold: float,
    computed_at: datetime,
) -> DriftSnapshot:
    """Assemble the snapshot for the latest proposal and live positions.

    Preconditions: ``proposal.weights`` sums to one (validated by ``Proposal``);
    ``rebalance_threshold`` is strictly positive; ``computed_at`` is
    timezone-aware (UTC internally). Postconditions: the returned snapshot is
    fully validated, including the internal consistency between the live
    weights and the reported drift.
    """
    if not math.isfinite(rebalance_threshold) or rebalance_threshold <= 0.0:
        raise ValueError("the rebalance threshold must be finite and positive")
    live = live_weights(positions, account_equity)
    drift = drift_l1(live, proposal.weights)
    total = math.fsum(proposal.weights.values())
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        raise ValueError("proposal weights must sum to one")
    return DriftSnapshot(
        proposal_id=proposal.proposal_id,
        run_date=proposal.run_date,
        weights_live=live,
        drift_l1=drift,
        rebalance_required=drift > rebalance_threshold,
        computed_at=computed_at,
    )
