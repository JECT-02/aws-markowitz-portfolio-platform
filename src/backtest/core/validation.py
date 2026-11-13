from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import product

import numpy as np

from backtest.core.engine import BacktestEngine
from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError
from optimizer.core.metrics import (
    annualized_return,
    annualized_volatility,
    max_drawdown,
    sharpe_ratio,
)
from optimizer.core.repository import ReturnsMatrix

TRAIN_FRACTION = 0.60
VALIDATION_FRACTION = 0.80
MIN_PERIOD_OBSERVATIONS = 2


@dataclass(frozen=True)
class PeriodMetrics:
    """Risk and return summary for an evaluation period."""

    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float


@dataclass(frozen=True)
class ParameterCandidate:
    """One hyperparameter combination evaluated across validation folds."""

    gamma: float
    transaction_cost_lambda: float
    alpha: float
    window_days: int
    validation_sharpe: float
    fold_metrics: list[PeriodMetrics]


@dataclass(frozen=True)
class ValidationResult:
    """Temporal validation outcome with separate in-sample and held-out metrics."""

    selected: ParameterCandidate
    candidates: list[ParameterCandidate]
    sensitivity: dict[int, PeriodMetrics]
    in_sample_metrics: dict[str, PeriodMetrics]
    out_of_sample_metrics: dict[str, PeriodMetrics]


class TemporalValidation:
    """Selects hyperparameters on validation and reports on held-out test data."""

    def __init__(self, universe: UniverseConfig, strategy: StrategyConfig) -> None:
        self._universe = universe
        self._strategy = strategy

    def run(self, matrix: ReturnsMatrix) -> ValidationResult:
        """Evaluate the configured grid with nested temporal folds."""
        train_end, validation_end = _split_indices(
            matrix,
            self._strategy.validation_windows,
            self._strategy.validation_folds,
        )
        folds = _fold_bounds(train_end, validation_end, self._strategy.validation_folds)
        candidates: list[ParameterCandidate] = []
        for gamma, cost_lambda, alpha, window in product(
            self._strategy.validation_gammas,
            self._strategy.validation_lambdas,
            self._strategy.validation_alphas,
            self._strategy.validation_windows,
        ):
            candidate_strategy = self._strategy.model_copy(
                update={
                    "risk_aversion": gamma,
                    "transaction_cost_lambda": cost_lambda,
                    "return_shrinkage_alpha": alpha,
                    "window_days": window,
                }
            )
            fold_metrics: list[PeriodMetrics] = []
            engine = BacktestEngine(candidate_strategy, self._universe)
            for fold_start, fold_end in folds:
                try:
                    result = engine.run(_slice(matrix, 0, fold_end), "optimizer")
                except DataValidationError:
                    break
                metrics = _period_metrics(
                    result.dates,
                    result.returns,
                    matrix.dates[fold_start],
                    matrix.dates[fold_end],
                    candidate_strategy.risk_free_rate_annual,
                )
                if metrics is None:
                    break
                fold_metrics.append(metrics)
            if len(fold_metrics) < len(folds):
                continue
            candidates.append(
                ParameterCandidate(
                    gamma=gamma,
                    transaction_cost_lambda=cost_lambda,
                    alpha=alpha,
                    window_days=window,
                    validation_sharpe=_mean_sharpe(fold_metrics),
                    fold_metrics=fold_metrics,
                )
            )
        if not candidates:
            raise DataValidationError("no parameter combination could be validated")
        candidates.sort(key=lambda item: item.validation_sharpe, reverse=True)
        selected = candidates[0]
        selected_strategy = self._strategy.model_copy(
            update={
                "risk_aversion": selected.gamma,
                "transaction_cost_lambda": selected.transaction_cost_lambda,
                "return_shrinkage_alpha": selected.alpha,
                "window_days": selected.window_days,
            }
        )
        engine = BacktestEngine(selected_strategy, self._universe)
        in_sample: dict[str, PeriodMetrics] = {}
        out_of_sample: dict[str, PeriodMetrics] = {}
        for mode in ("optimizer", "equal_weight", "minimum_variance", "spy"):
            result = engine.run(matrix, mode)
            in_metrics = _period_metrics(
                result.dates,
                result.returns,
                result.dates[0],
                matrix.dates[train_end],
                selected_strategy.risk_free_rate_annual,
            )
            if in_metrics is not None:
                in_sample[mode] = in_metrics
            out_metrics = _period_metrics(
                result.dates,
                result.returns,
                matrix.dates[validation_end],
                None,
                selected_strategy.risk_free_rate_annual,
            )
            if out_metrics is not None:
                out_of_sample[mode] = out_metrics
        if "optimizer" not in out_of_sample:
            raise DataValidationError("test period has no optimizer observations")
        return ValidationResult(
            selected=selected,
            candidates=candidates,
            sensitivity=_sensitivity(candidates),
            in_sample_metrics=in_sample,
            out_of_sample_metrics=out_of_sample,
        )


def _sensitivity(candidates: list[ParameterCandidate]) -> dict[int, PeriodMetrics]:
    """Return the last-fold validation metrics of the best candidate per window."""
    best_by_window: dict[int, ParameterCandidate] = {}
    for candidate in candidates:
        current = best_by_window.get(candidate.window_days)
        if current is None or candidate.validation_sharpe > current.validation_sharpe:
            best_by_window[candidate.window_days] = candidate
    return {
        window: candidate.fold_metrics[-1]
        for window, candidate in best_by_window.items()
    }


def _split_indices(
    matrix: ReturnsMatrix,
    windows: list[int],
    folds: int,
) -> tuple[int, int]:
    if not windows:
        raise DataValidationError("validation windows must not be empty")
    longest = max(windows)
    observations = matrix.n_observations
    train_end = int(observations * TRAIN_FRACTION)
    validation_end = int(observations * VALIDATION_FRACTION)
    if validation_end < longest * 2:
        raise DataValidationError("not enough observations for temporal validation")
    if validation_end - train_end < folds * MIN_PERIOD_OBSERVATIONS:
        raise DataValidationError("validation period is too short for the fold count")
    if observations - validation_end < MIN_PERIOD_OBSERVATIONS:
        raise DataValidationError("test period is too short")
    return train_end, validation_end


def _fold_bounds(
    train_end: int,
    validation_end: int,
    folds: int,
) -> list[tuple[int, int]]:
    """Return expanding-window fold slices inside the validation period."""
    span = validation_end - train_end
    step = span // folds
    bounds: list[tuple[int, int]] = []
    for index in range(folds):
        start = train_end + index * step
        end = validation_end if index == folds - 1 else train_end + (index + 1) * step
        bounds.append((start, end))
    return bounds


def _mean_sharpe(fold_metrics: list[PeriodMetrics]) -> float:
    """Return the mean Sharpe ratio across validation folds."""
    return float(np.mean([metrics.sharpe_ratio for metrics in fold_metrics]))


def _slice(matrix: ReturnsMatrix, start: int, end: int) -> ReturnsMatrix:
    return ReturnsMatrix(
        symbols=matrix.symbols,
        dates=matrix.dates[start:end],
        values=matrix.values[start:end],
        excluded=matrix.excluded,
    )


def _period_metrics(
    dates: list[date],
    returns: np.ndarray,
    start: date,
    end: date | None,
    risk_free_rate_annual: float,
) -> PeriodMetrics | None:
    mask = np.array(
        [date_value >= start and (end is None or date_value < end) for date_value in dates],
        dtype=bool,
    )
    selected = returns[mask]
    if selected.size < MIN_PERIOD_OBSERVATIONS:
        return None
    volatility = annualized_volatility(selected)
    if volatility <= 0.0:
        return None
    return PeriodMetrics(
        annualized_return=annualized_return(selected),
        annualized_volatility=volatility,
        sharpe_ratio=sharpe_ratio(selected, risk_free_rate_annual),
        max_drawdown=max_drawdown(selected),
    )
