import numpy as np
import pytest

from common.errors import DataValidationError, InfeasibleError
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
