from __future__ import annotations

import numpy as np

from common.errors import DataValidationError, InfeasibleError
from optimizer.core.problem import MarkowitzOptimizer

BOUND_TOLERANCE = 1e-8


def equal_weights(n_assets: int, max_weight: float) -> np.ndarray:
    """Return the 1/N portfolio if it satisfies the position limit."""
    if n_assets < 1:
        raise DataValidationError("at least one asset is required")
    weights = np.full(n_assets, 1.0 / n_assets)
    if float(weights.max()) > max_weight + BOUND_TOLERANCE:
        raise InfeasibleError("equal weights violate the position limit")
    return weights


def minimum_variance(covariance: np.ndarray, max_weight: float) -> np.ndarray:
    """Return the long-only minimum variance portfolio with position limits."""
    n_assets = int(covariance.shape[0])
    optimizer = MarkowitzOptimizer(n_assets, max_weight)
    result = optimizer.solve(
        covariance,
        np.zeros(n_assets),
        np.zeros(n_assets),
        gamma=1.0,
        transaction_cost_lambda=0.0,
    )
    return result.weights


def spy_weights(symbols: list[str], spy_symbol: str = "SPY") -> np.ndarray:
    """Return a one-hot allocation to the SPY benchmark."""
    if spy_symbol not in symbols:
        raise DataValidationError(f"{spy_symbol} is not part of the universe")
    weights = np.zeros(len(symbols))
    weights[symbols.index(spy_symbol)] = 1.0
    return weights
