from __future__ import annotations

import math
from datetime import UTC, date, datetime

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from common.models import BrokerPosition
from monitor.core.drift import drift_l1, drift_snapshot, live_weights
from optimizer.core.models import PortfolioMetrics, Proposal

MAX_VALUE = 1e12

position_schemas = st.lists(
    st.tuples(
        st.from_regex(r"[A-Z]{1,6}", fullmatch=True),
        st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    ),
    max_size=10,
)
equities = st.floats(min_value=1e-9, max_value=MAX_VALUE, allow_nan=False, allow_infinity=False)
weight_maps = st.dictionaries(
    st.from_regex(r"[A-Z]{1,6}", fullmatch=True),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    max_size=10,
)
thresholds = st.floats(min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False)

NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)


def _proposal(weights: dict[str, float]) -> Proposal:
    """Build a valid proposal with weights scaled to exactly one."""
    normalized = {symbol.upper(): max(0.0, value) for symbol, value in weights.items()}
    total = math.fsum(normalized.values())
    if total <= 0.0:
        normalized = {"SPY": 1.0}
    else:
        normalized = {symbol: value / total for symbol, value in normalized.items()}
    return Proposal(
        proposal_id="prop-prop",
        metadata={
            "run_id": "run-prop",
            "run_date": "2026-11-14",
            "code_version": "abc1234",
            "source": "alpaca",
            "fetched_at": NOW.isoformat(),
        },
        created_at=NOW,
        status="PROPOSED",
        rebalance_required=False,
        weights=normalized,
        actual_weights=normalized,
        drift_l1=0.0,
        benchmark_weights={"equal_weight": normalized},
        metrics={
            "markowitz": PortfolioMetrics(
                annualized_return=0.1,
                annualized_volatility=0.2,
                sharpe_ratio=0.3,
                sortino_ratio=0.4,
                max_drawdown=0.05,
                calmar_ratio=2.0,
                herfindahl_index=0.5,
            )
        },
        covariance_shrinkage=0.1,
        window_days=252,
        prices_ref="run_date=2026-11-14/prices.json",
        excluded={},
        run_date=date(2026, 11, 14),
    )


@settings(max_examples=200, deadline=None)
@given(
    positions=position_schemas,
    equity_multiple=st.floats(min_value=1.0, max_value=2.0, allow_nan=False, allow_infinity=False),
)
def test_live_weights_are_finite_non_negative_sorted_and_bounded(
    positions: list[tuple[str, float, float]],
    equity_multiple: float,
) -> None:
    """Weights derived from any consistent account are structurally valid.

    The equity is generated at or above the position valuation so the input
    satisfies ``live_weights``'s precondition (cash keeps the remainder).
    """
    parsed = [BrokerPosition(symbol=s, quantity=q, price=p) for s, q, p in positions]
    total = math.fsum(q * p for _, q, p in positions)
    equity = max(total, 1e-9) * equity_multiple
    weights = live_weights(parsed, equity)
    assert all(math.isfinite(weight) and weight >= 0.0 for weight in weights.values())
    assert math.fsum(weights.values()) <= 1.0 + 0.01 + 1e-9
    assert list(weights) == sorted(weights)


@settings(max_examples=200, deadline=None)
@given(live=weight_maps, star=weight_maps)
def test_drift_is_non_negative_and_symmetric(
    live: dict[str, float],
    star: dict[str, float],
) -> None:
    forward = drift_l1(live, star)
    backward = drift_l1(star, live)
    assert forward >= 0.0
    assert forward == pytest.approx(backward)


@settings(max_examples=200, deadline=None)
@given(
    positions=position_schemas,
    equity_multiple=st.floats(min_value=1.0, max_value=2.0, allow_nan=False, allow_infinity=False),
    star=weight_maps,
    threshold=thresholds,
)
def test_snapshot_matches_the_independently_recomputed_drift(
    positions: list[tuple[str, float, float]],
    equity_multiple: float,
    star: dict[str, float],
    threshold: float,
) -> None:
    parsed = [BrokerPosition(symbol=s, quantity=q, price=p) for s, q, p in positions]
    total = math.fsum(q * p for _, q, p in positions)
    equity = max(total, 1e-9) * equity_multiple
    proposal = _proposal(star)
    snapshot = drift_snapshot(proposal, parsed, equity, threshold, NOW)
    recomputed = drift_l1(snapshot.weights_live, proposal.weights)
    assert snapshot.drift_l1 == pytest.approx(recomputed)
    assert snapshot.rebalance_required is (snapshot.drift_l1 > threshold)
    assert snapshot.weights_live == live_weights(parsed, equity)
