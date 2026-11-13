from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from common.errors import DataValidationError, InfeasibleError
from optimizer.core.estimation import estimate, estimate_covariance, shrink_mean
from optimizer.core.problem import MarkowitzOptimizer

MAX_WEIGHT = 0.20
SETTINGS = settings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _returns(n_assets: int, rows: int, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    return generator.normal(0.0004, 0.01, size=(rows, n_assets))


@given(
    n_assets=st.integers(min_value=5, max_value=8),
    rows=st.integers(min_value=40, max_value=120),
    seed=st.integers(min_value=0, max_value=10_000),
)
@SETTINGS
def test_covariance_is_symmetric_and_positive_semidefinite(
    n_assets: int,
    rows: int,
    seed: int,
) -> None:
    covariance, shrinkage = estimate_covariance(_returns(n_assets, rows, seed))
    assert np.allclose(covariance, covariance.T)
    assert float(np.linalg.eigvalsh(covariance).min()) >= -1e-8
    assert 0.0 <= shrinkage <= 1.0


@given(
    n_assets=st.integers(min_value=5, max_value=8),
    rows=st.integers(min_value=40, max_value=120),
    seed=st.integers(min_value=0, max_value=10_000),
    alpha=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@SETTINGS
def test_optimizer_satisfies_budget_bounds_and_finiteness(
    n_assets: int,
    rows: int,
    seed: int,
    alpha: float,
) -> None:
    estimates = estimate(_returns(n_assets, rows, seed), alpha)
    actual = np.full(n_assets, 1.0 / n_assets)
    result = MarkowitzOptimizer(n_assets, MAX_WEIGHT).solve(
        estimates.covariance,
        estimates.mean,
        actual,
        gamma=2.0,
        transaction_cost_lambda=0.001,
    )
    weights = result.weights
    assert result.status == "optimal"
    assert weights.shape == (n_assets,)
    assert float(weights.sum()) == pytest.approx(1.0, abs=1e-6)
    assert float(weights.min()) >= 0.0
    assert float(weights.max()) <= MAX_WEIGHT + 1e-8
    assert bool(np.isfinite(weights).all())


@given(
    n_assets=st.integers(min_value=5, max_value=7),
    rows=st.integers(min_value=40, max_value=80),
    seed=st.integers(min_value=0, max_value=10_000),
)
@SETTINGS
def test_optimizer_is_deterministic(n_assets: int, rows: int, seed: int) -> None:
    estimates = estimate(_returns(n_assets, rows, seed), 0.5)
    actual = np.full(n_assets, 1.0 / n_assets)
    first = MarkowitzOptimizer(n_assets, MAX_WEIGHT).solve(
        estimates.covariance,
        estimates.mean,
        actual,
        gamma=2.0,
        transaction_cost_lambda=0.001,
    )
    second = MarkowitzOptimizer(n_assets, MAX_WEIGHT).solve(
        estimates.covariance,
        estimates.mean,
        actual,
        gamma=2.0,
        transaction_cost_lambda=0.001,
    )
    assert np.allclose(first.weights, second.weights, atol=1e-9)


@given(
    n_assets=st.integers(min_value=5, max_value=7),
    rows=st.integers(min_value=40, max_value=80),
    seed=st.integers(min_value=0, max_value=10_000),
)
@SETTINGS
def test_large_transaction_cost_keeps_actual_weights(
    n_assets: int,
    rows: int,
    seed: int,
) -> None:
    estimates = estimate(_returns(n_assets, rows, seed), 0.5)
    actual = np.full(n_assets, 1.0 / n_assets)
    # 1e4 already pins w to w_actual (the L1 term dominates the ~1e-3 mean by
    # ~1e7); larger magnitudes make the QP ill-conditioned and CLARABEL fails
    # numerically on degenerate Ledoit-Wolf covariances.
    result = MarkowitzOptimizer(n_assets, MAX_WEIGHT).solve(
        estimates.covariance,
        estimates.mean,
        actual,
        gamma=2.0,
        transaction_cost_lambda=1e4,
    )
    assert np.allclose(result.weights, actual, atol=1e-3)


@given(n_assets=st.integers(min_value=1, max_value=4))
def test_too_few_assets_fail_in_a_controlled_way(n_assets: int) -> None:
    with pytest.raises(InfeasibleError):
        MarkowitzOptimizer(n_assets, MAX_WEIGHT)


@given(
    rows=st.integers(min_value=10, max_value=60),
    n_assets=st.integers(min_value=2, max_value=6),
    alpha=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    seed=st.integers(min_value=0, max_value=10_000),
)
@SETTINGS
def test_shrunk_means_are_finite_and_bounded_by_global_mean(
    rows: int,
    n_assets: int,
    alpha: float,
    seed: int,
) -> None:
    returns = _returns(n_assets, rows, seed)
    shrunk = shrink_mean(returns, alpha)
    sample_mean = returns.mean(axis=0)
    global_mean = float(sample_mean.mean())
    assert bool(np.isfinite(shrunk).all())
    assert float(shrunk.min()) >= min(float(sample_mean.min()), global_mean) - 1e-12
    assert float(shrunk.max()) <= max(float(sample_mean.max()), global_mean) + 1e-12
    if alpha == 1.0:
        assert np.allclose(shrunk, global_mean)


@given(
    rows=st.integers(min_value=10, max_value=40),
    n_assets=st.integers(min_value=2, max_value=5),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_non_finite_returns_are_always_rejected(
    rows: int,
    n_assets: int,
    seed: int,
) -> None:
    returns = _returns(n_assets, rows, seed)
    returns[0, 0] = np.nan
    with pytest.raises(DataValidationError):
        estimate_covariance(returns)
