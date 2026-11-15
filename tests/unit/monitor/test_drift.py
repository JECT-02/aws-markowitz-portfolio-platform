from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest

from common.models import BrokerPosition
from monitor.core.drift import DriftSnapshot, drift_l1, drift_snapshot, live_weights
from optimizer.core.models import PortfolioMetrics, Proposal

NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)
_RUN_DATE = date(2026, 11, 14)


def _proposal(weights: dict[str, float]) -> Proposal:
    return Proposal(
        proposal_id="prop-1",
        metadata={
            "run_id": "run-1",
            "run_date": _RUN_DATE.isoformat(),
            "code_version": "abc1234",
            "source": "alpaca",
            "fetched_at": NOW.isoformat(),
        },
        created_at=NOW,
        status="PROPOSED",
        rebalance_required=False,
        weights=weights,
        actual_weights=weights,
        drift_l1=0.0,
        benchmark_weights={"equal_weight": weights},
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
        run_date=_RUN_DATE,
    )


def _positions(*values: tuple[str, float, float]) -> list[BrokerPosition]:
    return [BrokerPosition(symbol=s, quantity=q, price=p) for s, q, p in values]


class TestLiveWeights:
    def test_weights_equal_position_value_over_equity(self) -> None:
        positions = _positions(("SPY", 10.0, 100.0), ("TLT", 10.0, 100.0))
        weights = live_weights(positions, 2000.0)
        assert weights == {"SPY": pytest.approx(0.5), "TLT": pytest.approx(0.5)}

    def test_cash_remains_absent_and_sum_is_le_one(self) -> None:
        positions = _positions(("SPY", 10.0, 100.0))
        weights = live_weights(positions, 2000.0)
        assert math.fsum(weights.values()) == pytest.approx(0.5)
        assert "CASH" not in weights

    def test_empty_account_has_empty_weights(self) -> None:
        assert live_weights([], 1000.0) == {}

    def test_zero_equity_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="equity"):
            live_weights(_positions(("SPY", 1.0, 100.0)), 0.0)

    def test_values_beyond_the_valuation_tolerance_are_rejected(self) -> None:
        # 2000.0 in positions against 1000.0 equity breaks the valuation.
        with pytest.raises(ValueError, match="exceed the account equity"):
            live_weights(_positions(("SPY", 10.0, 100.0), ("TLT", 10.0, 100.0)), 1000.0)


class TestDriftL1:
    def test_identical_weights_have_zero_drift(self) -> None:
        weights = {"SPY": 0.6, "TLT": 0.4}
        assert drift_l1(weights, weights) == pytest.approx(0.0)

    def test_union_of_symbols_counts_full_weight_on_missing_side(self) -> None:
        assert drift_l1({"SPY": 0.5}, {"TLT": 0.5}) == pytest.approx(1.0)

    def test_drift_is_symmetric(self) -> None:
        live = {"SPY": 0.7, "TLT": 0.3}
        star = {"SPY": 0.2, "EFA": 0.8}
        assert drift_l1(live, star) == pytest.approx(drift_l1(star, live))

    def test_negative_weight_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="finite and non-negative"):
            drift_l1({"SPY": -0.5}, {"SPY": 0.5})


class TestDriftSnapshot:
    def test_snapshot_maps_proposal_and_positions(self) -> None:
        proposal = _proposal({"SPY": 0.6, "TLT": 0.4})
        positions = _positions(("SPY", 10.0, 100.0), ("TLT", 10.0, 100.0))
        snapshot = drift_snapshot(proposal, positions, 2000.0, 0.10, NOW)
        assert snapshot.proposal_id == "prop-1"
        assert snapshot.run_date == _RUN_DATE
        assert snapshot.drift_l1 == pytest.approx(0.2)
        assert snapshot.rebalance_required is True
        assert snapshot.computed_at == NOW

    def test_small_drift_does_not_require_rebalance(self) -> None:
        proposal = _proposal({"SPY": 0.55, "TLT": 0.45})
        positions = _positions(("SPY", 6.0, 100.0), ("TLT", 4.0, 100.0))
        snapshot = drift_snapshot(proposal, positions, 1000.0, 0.10, NOW)
        assert snapshot.drift_l1 == pytest.approx(0.1)
        # The threshold comparison is strict: drift equal to the threshold
        # does not trigger a rebalance.
        assert snapshot.rebalance_required is False

    def test_drift_beyond_the_threshold_requires_rebalance(self) -> None:
        proposal = _proposal({"SPY": 0.6, "TLT": 0.4})
        positions = _positions(("SPY", 15.0, 100.0), ("TLT", 5.0, 100.0))
        snapshot = drift_snapshot(proposal, positions, 2000.0, 0.10, NOW)
        assert snapshot.drift_l1 == pytest.approx(0.3)
        assert snapshot.rebalance_required is True

    def test_empty_portfolio_drifts_to_the_full_reference_weight(self) -> None:
        proposal = _proposal({"SPY": 0.6, "TLT": 0.4})
        snapshot = drift_snapshot(proposal, [], 1000.0, 0.10, NOW)
        assert snapshot.drift_l1 == pytest.approx(1.0)
        assert snapshot.weights_live == {}

    def test_unknown_live_symbol_counts_against_zero(self) -> None:
        proposal = _proposal({"SPY": 0.6, "TLT": 0.4})
        positions = _positions(("EFA", 10.0, 100.0))
        snapshot = drift_snapshot(proposal, positions, 2000.0, 0.10, NOW)
        assert snapshot.drift_l1 == pytest.approx(1.5)

    def test_non_positive_threshold_is_rejected(self) -> None:
        proposal = _proposal({"SPY": 1.0})
        with pytest.raises(ValueError, match="threshold"):
            drift_snapshot(proposal, [], 1000.0, 0.0, NOW)

    def test_snapshot_rejects_an_inconsistent_drift(self) -> None:
        with pytest.raises(ValueError, match="inconsistent"):
            DriftSnapshot(
                proposal_id="prop-1",
                run_date=_RUN_DATE,
                weights_live={"SPY": 1.008},
                drift_l1=0.0,
                rebalance_required=False,
                computed_at=NOW,
            )

    def test_snapshot_rejects_negative_drift(self) -> None:
        with pytest.raises(ValueError, match="drift must be finite"):
            DriftSnapshot(
                proposal_id="prop-1",
                run_date=_RUN_DATE,
                weights_live={},
                drift_l1=-0.5,
                rebalance_required=False,
                computed_at=NOW,
            )

    def test_snapshot_rejects_weights_beyond_one(self) -> None:
        with pytest.raises(ValueError, match="valuation tolerance"):
            DriftSnapshot(
                proposal_id="prop-1",
                run_date=_RUN_DATE,
                weights_live={"SPY": 1.05},
                drift_l1=0.05,
                rebalance_required=False,
                computed_at=NOW,
            )
