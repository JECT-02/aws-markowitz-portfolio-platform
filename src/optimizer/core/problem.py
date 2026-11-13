from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cvxpy
import numpy as np

from common.errors import DataValidationError, InfeasibleError, SolverError

cp: Any = cvxpy

MIN_EIGENVALUE = -1e-8
SUM_TOLERANCE = 1e-6
BOUND_TOLERANCE = 1e-8
MIN_ASSETS = 5
INACCURATE_STATUS = "optimal_inaccurate"
INACCURATE_TOLERANCE = 1e-4
OPTIMAL_STATUSES = frozenset({"optimal", INACCURATE_STATUS})


@dataclass(frozen=True)
class OptimizationResult:
    """Validated weights returned by the optimizer."""

    weights: np.ndarray
    status: str
    objective: float


class MarkowitzOptimizer:
    """Mean-variance optimizer with turnover penalty and position limits."""

    def __init__(self, n_assets: int, max_weight: float, solver: str = "CLARABEL") -> None:
        if n_assets < MIN_ASSETS:
            raise InfeasibleError(f"at least {MIN_ASSETS} assets are required")
        if not 0.0 < max_weight <= 1.0:
            raise DataValidationError("max_weight must be within (0, 1]")
        if n_assets * max_weight < 1.0 - BOUND_TOLERANCE:
            raise InfeasibleError("max_weight cannot sum to one for this universe")
        self._n_assets = n_assets
        self._max_weight = max_weight
        self._solver = solver
        self._weights = cp.Variable(n_assets)
        self._covariance = cp.Parameter((n_assets, n_assets), PSD=True)
        self._mean = cp.Parameter(n_assets)
        self._actual = cp.Parameter(n_assets)
        self._gamma = cp.Parameter(nonneg=True)
        self._lambda = cp.Parameter(nonneg=True)
        objective = cp.Minimize(
            self._gamma / 2.0 * cp.quad_form(self._weights, self._covariance)
            - self._mean @ self._weights
            + self._lambda * cp.norm1(self._weights - self._actual)
        )
        constraints = [
            cp.sum(self._weights) == 1,
            self._weights >= 0,
            self._weights <= max_weight,
        ]
        self._problem = cp.Problem(objective, constraints)

    def solve(
        self,
        covariance: np.ndarray,
        mean: np.ndarray,
        actual_weights: np.ndarray,
        gamma: float,
        transaction_cost_lambda: float,
    ) -> OptimizationResult:
        """Solve the convex problem and return validated weights."""
        self._validate_inputs(covariance, mean, actual_weights, gamma, transaction_cost_lambda)
        self._covariance.value = covariance
        self._mean.value = mean
        self._actual.value = actual_weights
        self._gamma.value = gamma
        self._lambda.value = transaction_cost_lambda
        try:
            self._problem.solve(solver=self._solver, verbose=False, ignore_dpp=True)
        except cp.SolverError as exc:
            raise SolverError("solver failed") from exc
        status = str(self._problem.status)
        if status in {"infeasible", "infeasible_inaccurate"}:
            raise InfeasibleError("optimization problem is infeasible")
        if status not in OPTIMAL_STATUSES:
            raise SolverError(f"unexpected solver status: {status}")
        if self._weights.value is None:
            raise SolverError("solver returned no weights")
        if status == INACCURATE_STATUS:
            tolerance = INACCURATE_TOLERANCE
            sum_tolerance = INACCURATE_TOLERANCE
        else:
            tolerance = BOUND_TOLERANCE
            sum_tolerance = SUM_TOLERANCE
        weights = self._clean_weights(
            np.asarray(self._weights.value, dtype=float),
            tolerance=tolerance,
            sum_tolerance=sum_tolerance,
        )
        return OptimizationResult(
            weights=weights,
            status=status,
            objective=float(self._problem.value),
        )

    def _clean_weights(
        self,
        weights: np.ndarray,
        tolerance: float = BOUND_TOLERANCE,
        sum_tolerance: float = SUM_TOLERANCE,
    ) -> np.ndarray:
        if weights.shape != (self._n_assets,):
            raise SolverError("solver returned weights with an invalid shape")
        if not np.isfinite(weights).all():
            raise SolverError("solver returned non-finite weights")
        if float(weights.min()) < -tolerance:
            raise SolverError("solver violated the long-only constraint")
        if abs(float(weights.sum()) - 1.0) > sum_tolerance:
            raise SolverError("solver violated the budget constraint")
        cleaned = np.clip(weights, 0.0, self._max_weight)
        total = float(cleaned.sum())
        if total <= 0.0:
            raise SolverError("solver returned an empty portfolio")
        cleaned = cleaned / total
        if float(cleaned.max()) > self._max_weight + tolerance:
            raise SolverError("solver violated the position limit")
        return np.asarray(cleaned, dtype=float)

    def _validate_inputs(
        self,
        covariance: np.ndarray,
        mean: np.ndarray,
        actual_weights: np.ndarray,
        gamma: float,
        transaction_cost_lambda: float,
    ) -> None:
        if covariance.shape != (self._n_assets, self._n_assets):
            raise DataValidationError("covariance has an invalid shape")
        if mean.shape != (self._n_assets,):
            raise DataValidationError("expected returns have an invalid shape")
        if actual_weights.shape != (self._n_assets,):
            raise DataValidationError("actual weights have an invalid shape")
        if not np.isfinite(covariance).all():
            raise DataValidationError("covariance contains non-finite values")
        if not np.isfinite(mean).all():
            raise DataValidationError("expected returns contain non-finite values")
        if not np.isfinite(actual_weights).all():
            raise DataValidationError("actual weights contain non-finite values")
        if not np.allclose(covariance, covariance.T, atol=1e-10):
            raise DataValidationError("covariance is not symmetric")
        if float(np.linalg.eigvalsh(covariance).min()) < MIN_EIGENVALUE:
            raise DataValidationError("covariance is not positive semidefinite")
        if gamma <= 0.0:
            raise DataValidationError("gamma must be positive")
        if transaction_cost_lambda < 0.0:
            raise DataValidationError("transaction cost lambda must be non-negative")
