from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.covariance import LedoitWolf

from common.errors import DataValidationError, SolverError

MIN_COVARIANCE_EIGENVALUE = -1e-8


@dataclass(frozen=True)
class Estimates:
    """Expected returns and covariance estimate for a return matrix."""

    mean: np.ndarray
    covariance: np.ndarray
    shrinkage: float


def estimate_covariance(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate a symmetric PSD Ledoit-Wolf covariance and its shrinkage."""
    matrix = _as_matrix(returns)
    if matrix.shape[0] < 2:
        raise DataValidationError("at least two observations are required")
    model = LedoitWolf().fit(matrix)
    covariance = np.asarray(model.covariance_, dtype=float)
    covariance = (covariance + covariance.T) / 2.0
    if not np.isfinite(covariance).all():
        raise SolverError("covariance contains non-finite values")
    eigenvalues = np.linalg.eigvalsh(covariance)
    if float(eigenvalues.min()) < MIN_COVARIANCE_EIGENVALUE:
        raise SolverError("covariance is not positive semidefinite")
    return covariance, float(model.shrinkage_)


def shrink_mean(returns: np.ndarray, alpha: float) -> np.ndarray:
    """Shrink sample means toward the global mean with strength alpha."""
    matrix = _as_matrix(returns)
    if not 0.0 <= alpha <= 1.0:
        raise DataValidationError("shrinkage alpha must be within [0, 1]")
    sample_mean = matrix.mean(axis=0)
    global_mean = float(sample_mean.mean())
    shrunk = (1.0 - alpha) * sample_mean + alpha * global_mean
    if not np.isfinite(shrunk).all():
        raise SolverError("expected returns contain non-finite values")
    return np.asarray(shrunk, dtype=float)


def estimate(returns: np.ndarray, alpha: float) -> Estimates:
    """Estimate covariance and shrunk expected returns."""
    covariance, shrinkage = estimate_covariance(returns)
    return Estimates(
        mean=shrink_mean(returns, alpha),
        covariance=covariance,
        shrinkage=shrinkage,
    )


def _as_matrix(returns: np.ndarray) -> np.ndarray:
    matrix = np.asarray(returns, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 1:
        raise DataValidationError("returns must be a two-dimensional matrix")
    if not np.isfinite(matrix).all():
        raise DataValidationError("returns contain non-finite values")
    return matrix
