from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from common.config import StrategyConfig, UniverseConfig
from common.errors import ConfigurationError, InfeasibleError, SolverError
from common.models import DataSource, RunMetadata
from optimizer.core.models import Proposal
from optimizer.handler import OptimizerDependencies, run_optimizer


class _Clock:
    def now(self) -> datetime:
        return datetime(2025, 2, 3, 21, 30, tzinfo=UTC)

    def today(self) -> date:
        return self.now().date()


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[RunMetadata, dict[str, float] | None]] = []

    def run(
        self,
        metadata: RunMetadata,
        actual_weights: dict[str, float] | None = None,
    ) -> Proposal:
        self.calls.append((metadata, actual_weights))
        return Proposal(
            proposal_id=f"{metadata.run_date.isoformat()}-{metadata.run_id}",
            metadata=metadata,
            created_at=datetime(2025, 2, 3, 21, 0, tzinfo=UTC),
            status="PENDING",
            rebalance_required=True,
            weights={"SPY": 0.2},
            actual_weights={"SPY": 1.0},
            drift_l1=0.8,
            benchmark_weights={},
            metrics={},
            covariance_shrinkage=0.1,
            window_days=252,
            prices_ref="2024-02-01..2025-01-31",
            excluded={"QQQ": "no_data"},
            run_date=metadata.run_date,
        )


class _FailingService(_Service):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def run(
        self,
        metadata: RunMetadata,
        actual_weights: dict[str, float] | None = None,
    ) -> Proposal:
        raise self.error


class _State:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights
        self.reads = 0
        self.proposals: list[Proposal] = []
        self.failures: list[tuple[str, RunMetadata, str]] = []

    def read_actual_weights(self) -> dict[str, float] | None:
        self.reads += 1
        return self.weights

    def write_proposal(self, proposal: Proposal) -> None:
        self.proposals.append(proposal)

    def write_failure(self, proposal_id: str, metadata: RunMetadata, reason: str) -> None:
        self.failures.append((proposal_id, metadata, reason))


def _deps(service: _Service, state: _State | None = None) -> OptimizerDependencies:
    strategy = StrategyConfig.model_validate(
        {
            "risk_aversion": 2.0,
            "transaction_cost_lambda": 0.001,
            "return_shrinkage_alpha": 0.5,
            "risk_free_rate_annual": 0.04,
            "max_forward_fill_days": 2,
            "window_days": 252,
            "ingest_lookback_days": 420,
            "rebalance_l1_threshold": 0.10,
            "validation_gammas": [1.0],
            "validation_lambdas": [0.0],
            "validation_alphas": [0.5],
            "validation_windows": [252],
            "validation_folds": 3,
        }
    )
    return OptimizerDependencies(
        service=service,
        clock=_Clock(),
        universe=UniverseConfig(tickers=["SPY", "QQQ"], max_weight=0.6),
        strategy=strategy,
        code_version="test",
        state=state,
    )


def test_run_optimizer_builds_metadata_and_response() -> None:
    service = _Service()
    response = run_optimizer(
        {
            "run_date": "2025-02-03",
            "run_id": "run-9",
            "source": DataSource.YFINANCE.value,
            "actual_weights": {"SPY": 0.6, "QQQ": 0.4},
        },
        _deps(service),
    )
    metadata, actual = service.calls[0]
    assert metadata.run_id == "run-9"
    assert metadata.run_date == date(2025, 2, 3)
    assert metadata.source == DataSource.YFINANCE
    assert actual == {"SPY": 0.6, "QQQ": 0.4}
    assert response["proposal_id"] == "2025-02-03-run-9"
    assert response["status"] == "PENDING"
    assert response["rebalance_required"] is True
    assert response["excluded"] == {"QQQ": "no_data"}


def test_run_optimizer_without_actual_weights() -> None:
    service = _Service()
    run_optimizer({"run_date": "2025-02-03"}, _deps(service))
    _, actual = service.calls[0]
    assert actual is None


def test_invalid_actual_weights_are_rejected() -> None:
    with pytest.raises(ConfigurationError):
        run_optimizer({"actual_weights": [0.5, 0.5]}, _deps(_Service()))


def test_non_numeric_actual_weights_are_rejected() -> None:
    with pytest.raises(ConfigurationError):
        run_optimizer({"actual_weights": {"SPY": "0.5"}}, _deps(_Service()))


def test_unsupported_source_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        run_optimizer({"source": "nasdaq"}, _deps(_Service()))


def test_state_weights_are_used_when_event_has_none() -> None:
    state = _State({"SPY": 0.6, "QQQ": 0.4})
    service = _Service()
    run_optimizer({"run_date": "2025-02-03"}, _deps(service, state))
    assert state.reads == 1
    _, actual = service.calls[0]
    assert actual == {"SPY": 0.6, "QQQ": 0.4}


def test_event_weights_take_precedence_over_state() -> None:
    state = _State({"SPY": 1.0})
    service = _Service()
    run_optimizer(
        {"run_date": "2025-02-03", "actual_weights": {"SPY": 0.6, "QQQ": 0.4}},
        _deps(service, state),
    )
    assert state.reads == 0
    _, actual = service.calls[0]
    assert actual == {"SPY": 0.6, "QQQ": 0.4}


def test_successful_proposal_is_persisted() -> None:
    state = _State()
    response = run_optimizer({"run_date": "2025-02-03"}, _deps(_Service(), state))
    assert len(state.proposals) == 1
    assert state.proposals[0].proposal_id == response["proposal_id"]
    assert state.failures == []


def test_solver_failure_persists_a_failed_proposal() -> None:
    state = _State()
    with pytest.raises(SolverError):
        run_optimizer({"run_date": "2025-02-03"}, _deps(_FailingService(SolverError("bad")), state))
    proposal_id, metadata, reason = state.failures[0]
    assert proposal_id == f"2025-02-03-{metadata.run_id}"
    assert metadata.run_date == date(2025, 2, 3)
    assert reason == "bad"
    assert state.proposals == []


def test_infeasible_failure_persists_a_failed_proposal() -> None:
    state = _State()
    with pytest.raises(InfeasibleError):
        run_optimizer(
            {"run_date": "2025-02-03"},
            _deps(_FailingService(InfeasibleError("few assets")), state),
        )
    assert state.failures[0][2] == "few assets"
