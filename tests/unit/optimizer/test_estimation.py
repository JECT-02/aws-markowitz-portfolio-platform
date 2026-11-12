import numpy as np
import pytest

from common.errors import DataValidationError
from optimizer.core.estimation import estimate, estimate_covariance, shrink_mean


def _returns(seed: int = 7, rows: int = 120, columns: int = 6) -> np.ndarray:
    generator = np.random.default_rng(seed)
    return generator.normal(0.0005, 0.01, size=(rows, columns))


def test_covariance_is_symmetric_and_psd() -> None:
    covariance, shrinkage = estimate_covariance(_returns())
    assert covariance.shape == (6, 6)
    assert np.allclose(covariance, covariance.T)
    assert float(np.linalg.eigvalsh(covariance).min()) >= -1e-8
    assert 0.0 <= shrinkage <= 1.0


def test_shrink_mean_with_zero_alpha_is_sample_mean() -> None:
    returns = _returns()
    assert np.allclose(shrink_mean(returns, 0.0), returns.mean(axis=0))


def test_shrink_mean_with_full_alpha_is_constant() -> None:
    shrunk = shrink_mean(_returns(), 1.0)
    assert np.allclose(shrunk, shrunk.mean())
    assert float(shrunk.std()) == pytest.approx(0.0)


def test_estimate_returns_consistent_shapes() -> None:
    estimates = estimate(_returns(), 0.4)
    assert estimates.mean.shape == (6,)
    assert estimates.covariance.shape == (6, 6)
    assert 0.0 <= estimates.shrinkage <= 1.0


def test_invalid_alpha_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        shrink_mean(_returns(), 1.5)


def test_non_finite_returns_are_rejected() -> None:
    returns = _returns()
    returns[0, 0] = np.nan
    with pytest.raises(DataValidationError):
        estimate_covariance(returns)


def test_one_dimensional_returns_are_rejected() -> None:
    with pytest.raises(DataValidationError):
        estimate_covariance(np.zeros(10))


def test_single_observation_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        estimate_covariance(np.zeros((1, 4)))
