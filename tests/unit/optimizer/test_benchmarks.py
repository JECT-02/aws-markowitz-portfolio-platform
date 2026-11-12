import numpy as np
import pytest

from common.errors import DataValidationError, InfeasibleError
from optimizer.core.benchmarks import equal_weights, minimum_variance, spy_weights


def _covariance(seed: int = 3) -> np.ndarray:
    generator = np.random.default_rng(seed)
    returns = generator.normal(0.0, 0.01, size=(200, 6))
    return np.cov(returns, rowvar=False) + np.eye(6) * 1e-8


def test_equal_weights_for_feasible_universe() -> None:
    assert np.allclose(equal_weights(5, 0.20), np.full(5, 0.20))
    assert np.allclose(equal_weights(10, 0.20), np.full(10, 0.10))


def test_equal_weights_infeasible_limit_is_rejected() -> None:
    with pytest.raises(InfeasibleError):
        equal_weights(4, 0.20)


def test_minimum_variance_satisfies_bounds() -> None:
    weights = minimum_variance(_covariance(), 0.20)
    assert weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert float(weights.min()) >= 0.0
    assert float(weights.max()) <= 0.20 + 1e-8


def test_spy_weights_are_one_hot() -> None:
    weights = spy_weights(["SPY", "QQQ", "IWM"])
    assert weights.tolist() == [1.0, 0.0, 0.0]


def test_spy_weights_require_spy_in_universe() -> None:
    with pytest.raises(DataValidationError):
        spy_weights(["QQQ", "IWM"])
