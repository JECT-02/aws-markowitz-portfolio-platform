from typing import Any

import cvxpy
import numpy as np
import pytest

from common.errors import DataValidationError, InfeasibleError, SolverError
from optimizer.core.problem import MarkowitzOptimizer


def _inputs(n_assets: int = 6, seed: int = 11) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    generator = np.random.default_rng(seed)
    returns = generator.normal(0.0008, 0.01, size=(200, n_assets))
    covariance = np.cov(returns, rowvar=False) + np.eye(n_assets) * 1e-8
    mean = returns.mean(axis=0)
    actual = np.full(n_assets, 1.0 / n_assets)
    return covariance, mean, actual


def test_weights_satisfy_budget_bounds_and_finiteness() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, actual = _inputs()
    result = optimizer.solve(covariance, mean, actual, gamma=2.0, transaction_cost_lambda=0.001)
    weights = result.weights
    assert result.status == "optimal"
    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert float(weights.min()) >= 0.0
    assert float(weights.max()) <= 0.20 + 1e-8
    assert bool(np.isfinite(weights).all())


def test_large_transaction_cost_keeps_actual_weights() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, _ = _inputs()
    actual = np.array([0.20, 0.20, 0.20, 0.20, 0.10, 0.10])
    result = optimizer.solve(covariance, mean, actual, gamma=2.0, transaction_cost_lambda=1e6)
    assert np.allclose(result.weights, actual, atol=1e-3)


def test_solution_is_deterministic() -> None:
    covariance, mean, actual = _inputs()
    first = MarkowitzOptimizer(6, 0.20).solve(covariance, mean, actual, 2.0, 0.001)
    second = MarkowitzOptimizer(6, 0.20).solve(covariance, mean, actual, 2.0, 0.001)
    assert np.array_equal(first.weights, second.weights)


def test_too_few_assets_is_rejected() -> None:
    with pytest.raises(InfeasibleError):
        MarkowitzOptimizer(4, 0.20)


def test_infeasible_position_limit_is_rejected() -> None:
    with pytest.raises(InfeasibleError):
        MarkowitzOptimizer(6, 0.10)


def test_invalid_covariance_shape_is_rejected() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, actual = _inputs()
    with pytest.raises(DataValidationError):
        optimizer.solve(covariance[:5, :5], mean, actual, 2.0, 0.001)


def test_non_symmetric_covariance_is_rejected() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, actual = _inputs()
    broken = covariance.copy()
    broken[0, 1] += 1.0
    with pytest.raises(DataValidationError):
        optimizer.solve(broken, mean, actual, 2.0, 0.001)


def test_non_psd_covariance_is_rejected() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    mean = np.zeros(6)
    actual = np.full(6, 1.0 / 6.0)
    broken = -np.eye(6)
    with pytest.raises(DataValidationError):
        optimizer.solve(broken, mean, actual, 2.0, 0.001)


def test_invalid_gamma_is_rejected() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, actual = _inputs()
    with pytest.raises(DataValidationError):
        optimizer.solve(covariance, mean, actual, 0.0, 0.001)


def test_negative_transaction_cost_is_rejected() -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, actual = _inputs()
    with pytest.raises(DataValidationError):
        optimizer.solve(covariance, mean, actual, 2.0, -0.001)


@pytest.mark.parametrize("max_weight", [0.0, -0.1, 1.5])
def test_invalid_max_weight_is_rejected(max_weight: float) -> None:
    with pytest.raises(DataValidationError):
        MarkowitzOptimizer(6, max_weight)


class _StubProblem:
    def __init__(
        self,
        status: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.status = status
        self.value = 0.0
        self._error = error

    def solve(self, **_kwargs: Any) -> None:
        if self._error is not None:
            raise self._error


def _with_stub(
    status: str | None = None,
    error: Exception | None = None,
) -> MarkowitzOptimizer:
    optimizer = MarkowitzOptimizer(6, 0.20)
    optimizer._problem = _StubProblem(status=status, error=error)  # type: ignore[assignment]
    return optimizer


def test_solver_exception_is_wrapped() -> None:
    optimizer = _with_stub(error=cvxpy.SolverError("boom"))
    covariance, mean, actual = _inputs()
    with pytest.raises(SolverError, match="solver failed"):
        optimizer.solve(covariance, mean, actual, 2.0, 0.001)


@pytest.mark.parametrize("status", ["infeasible", "infeasible_inaccurate"])
def test_infeasible_status_is_reported(status: str) -> None:
    optimizer = _with_stub(status=status)
    covariance, mean, actual = _inputs()
    with pytest.raises(InfeasibleError, match="infeasible"):
        optimizer.solve(covariance, mean, actual, 2.0, 0.001)


def test_unexpected_status_is_rejected() -> None:
    optimizer = _with_stub(status="unbounded")
    covariance, mean, actual = _inputs()
    with pytest.raises(SolverError, match="unexpected solver status"):
        optimizer.solve(covariance, mean, actual, 2.0, 0.001)


def test_inaccurate_status_is_accepted_with_explicit_tolerance() -> None:
    optimizer = _with_stub(status="optimal_inaccurate")
    optimizer._weights.value = np.full(6, 1.0 / 6.0)
    covariance, mean, actual = _inputs()
    result = optimizer.solve(covariance, mean, actual, 2.0, 1e7)
    assert result.status == "optimal_inaccurate"
    assert float(result.weights.sum()) == pytest.approx(1.0, abs=1e-6)


def test_missing_weights_are_rejected() -> None:
    optimizer = _with_stub(status="optimal")
    covariance, mean, actual = _inputs()
    with pytest.raises(SolverError, match="no weights"):
        optimizer.solve(covariance, mean, actual, 2.0, 0.001)


@pytest.mark.parametrize(
    ("weights", "message"),
    [
        (np.full(5, 0.2), "invalid shape"),
        (np.array([np.inf, 0.2, 0.2, 0.2, 0.2, 0.2]), "non-finite"),
        (np.array([-0.1, 0.3, 0.2, 0.2, 0.2, 0.2]), "long-only"),
        (np.array([0.1, 0.1, 0.1, 0.1, 0.1, 0.1]), "budget"),
        (np.array([0.25, 0.15, 0.15, 0.15, 0.15, 0.15]), "position limit"),
    ],
)
def test_invalid_solver_weights_are_rejected(weights: np.ndarray, message: str) -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    with pytest.raises(SolverError, match=message):
        optimizer._clean_weights(weights)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda covariance, mean, actual: (covariance, mean[:4], actual),
        lambda covariance, mean, actual: (covariance, mean, actual[:4]),
        lambda covariance, mean, actual: (
            np.full_like(covariance, np.nan),
            mean,
            actual,
        ),
        lambda covariance, mean, actual: (covariance, np.full_like(mean, np.inf), actual),
        lambda covariance, mean, actual: (covariance, mean, np.full_like(actual, np.nan)),
    ],
)
def test_invalid_input_shapes_and_values_are_rejected(mutate: Any) -> None:
    optimizer = MarkowitzOptimizer(6, 0.20)
    covariance, mean, actual = _inputs()
    broken_covariance, broken_mean, broken_actual = mutate(covariance, mean, actual)
    with pytest.raises(DataValidationError):
        optimizer.solve(broken_covariance, broken_mean, broken_actual, 2.0, 0.001)
