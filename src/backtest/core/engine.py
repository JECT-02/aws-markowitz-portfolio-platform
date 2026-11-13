from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np

from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError
from optimizer.core.benchmarks import equal_weights, spy_weights
from optimizer.core.estimation import estimate, estimate_covariance
from optimizer.core.metrics import (
    annualized_return,
    annualized_volatility,
    calmar_ratio,
    herfindahl_index,
    max_drawdown,
    portfolio_log_returns,
    sharpe_ratio,
    sortino_ratio,
    turnover_l1,
)
from optimizer.core.models import PortfolioMetrics
from optimizer.core.problem import MarkowitzOptimizer
from optimizer.core.repository import ReturnsMatrix

MODES = frozenset({"optimizer", "equal_weight", "minimum_variance", "spy"})
INITIAL_EQUITY = 1.0
MIN_HISTORY_MULTIPLIER = 2


@dataclass(frozen=True)
class Rebalance:
    """One rebalance decision recorded by the backtest."""

    date: date
    weights: np.ndarray
    drift_l1: float
    cost: float


@dataclass(frozen=True)
class BacktestResult:
    """Daily out-of-sample result for one allocation rule."""

    mode: str
    dates: list[date]
    returns: np.ndarray
    equity: np.ndarray
    final_weights: np.ndarray
    rebalances: list[Rebalance]
    metrics: PortfolioMetrics


class BacktestEngine:
    """Walk-forward engine with monthly rebalancing and transaction costs."""

    def __init__(self, strategy: StrategyConfig, universe: UniverseConfig) -> None:
        self._strategy = strategy
        self._universe = universe

    def run(self, matrix: ReturnsMatrix, mode: str = "optimizer") -> BacktestResult:
        """Run a walk-forward backtest for one allocation rule."""
        if mode not in MODES:
            raise DataValidationError(f"unsupported backtest mode: {mode}")
        self._validate_matrix(matrix)
        window = self._strategy.window_days
        if mode == "spy":
            return self._run_spy(matrix)
        rebalance_rows = _monthly_indices(matrix.dates, window)
        if not rebalance_rows:
            raise DataValidationError("not enough history for a rebalance")
        rebalance_set = set(rebalance_rows)
        provider = self._provider(matrix.symbols, mode)
        use_threshold = mode == "optimizer"
        initial = equal_weights(matrix.n_assets, self._universe.max_weight)
        current = initial.copy()
        returns: list[float] = []
        dates: list[date] = []
        rebalances: list[Rebalance] = []
        for row in range(rebalance_rows[0], len(matrix.dates)):
            cost = 0.0
            if row in rebalance_set:
                training = matrix.values[row - window : row]
                target = provider(training, current)
                drift = turnover_l1(target, current)
                should_trade = (
                    drift > 0.0
                    if not use_threshold
                    else (drift > self._strategy.rebalance_l1_threshold)
                )
                if should_trade:
                    cost = self._strategy.transaction_cost_lambda * drift
                    current = target.copy()
                rebalances.append(
                    Rebalance(
                        date=matrix.dates[row],
                        weights=current.copy(),
                        drift_l1=drift,
                        cost=cost,
                    )
                )
            day = matrix.values[row]
            net_return = _net_log_return(day, current, cost)
            returns.append(net_return)
            dates.append(matrix.dates[row])
            current = _drift_weights(current, day)
        daily = np.asarray(returns, dtype=float)
        if not np.isfinite(daily).all():
            raise DataValidationError("backtest produced non-finite returns")
        equity = INITIAL_EQUITY * np.exp(np.cumsum(daily))
        return BacktestResult(
            mode=mode,
            dates=dates,
            returns=daily,
            equity=equity,
            final_weights=current.copy(),
            rebalances=rebalances,
            metrics=_metrics(daily, current, self._strategy.risk_free_rate_annual),
        )

    def _provider(
        self,
        symbols: list[str],
        mode: str,
    ) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
        if mode == "equal_weight":
            return lambda _training, _actual: equal_weights(len(symbols), self._universe.max_weight)
        if mode == "minimum_variance":
            optimizer = MarkowitzOptimizer(len(symbols), self._universe.max_weight)
            zeros = np.zeros(len(symbols))

            def minimum_variance_target(
                training: np.ndarray,
                _actual: np.ndarray,
            ) -> np.ndarray:
                covariance, _ = estimate_covariance(training)
                return optimizer.solve(
                    covariance,
                    zeros,
                    zeros,
                    gamma=1.0,
                    transaction_cost_lambda=0.0,
                ).weights

            return minimum_variance_target
        optimizer = MarkowitzOptimizer(len(symbols), self._universe.max_weight)

        def optimizer_target(training: np.ndarray, actual: np.ndarray) -> np.ndarray:
            estimates = estimate(training, self._strategy.return_shrinkage_alpha)
            return optimizer.solve(
                estimates.covariance,
                estimates.mean,
                actual,
                gamma=self._strategy.risk_aversion,
                transaction_cost_lambda=self._strategy.transaction_cost_lambda,
            ).weights

        return optimizer_target

    def _run_spy(self, matrix: ReturnsMatrix) -> BacktestResult:
        window = self._strategy.window_days
        weights = spy_weights(matrix.symbols)
        returns = portfolio_log_returns(matrix.values[window:], weights)
        dates = matrix.dates[window:]
        equity = INITIAL_EQUITY * np.exp(np.cumsum(returns))
        return BacktestResult(
            mode="spy",
            dates=dates,
            returns=np.asarray(returns, dtype=float),
            equity=equity,
            final_weights=weights,
            rebalances=[],
            metrics=_metrics(returns, weights, self._strategy.risk_free_rate_annual),
        )

    def _validate_matrix(self, matrix: ReturnsMatrix) -> None:
        if matrix.n_assets < 5:
            raise DataValidationError("backtest requires at least five assets")
        if matrix.n_observations < self._strategy.window_days * MIN_HISTORY_MULTIPLIER:
            raise DataValidationError("not enough observations for a walk-forward backtest")
        if matrix.dates != sorted(matrix.dates):
            raise DataValidationError("returns must be ordered by date")
        if len(set(matrix.dates)) != len(matrix.dates):
            raise DataValidationError("returns must be unique by date")


def _monthly_indices(dates: list[date], window: int) -> list[int]:
    indices: list[int] = []
    seen: set[tuple[int, int]] = set()
    for index in range(window, len(dates)):
        key = (dates[index].year, dates[index].month)
        if key not in seen:
            seen.add(key)
            indices.append(index)
    return indices


def _drift_weights(weights: np.ndarray, day_returns: np.ndarray) -> np.ndarray:
    growth = weights * np.exp(day_returns)
    total = float(growth.sum())
    if total <= 0.0:
        raise DataValidationError("portfolio value collapsed during backtest")
    return np.asarray(growth / total, dtype=float)


def _net_log_return(
    day_asset_log_returns: np.ndarray,
    weights: np.ndarray,
    cost: float,
) -> float:
    """Return the day's portfolio log return net of the transaction cost."""
    gross_simple = float(np.expm1(day_asset_log_returns) @ weights)
    net_simple = gross_simple - cost
    if net_simple <= -1.0:
        raise DataValidationError("portfolio value collapsed during backtest")
    return float(np.log1p(net_simple))


def _metrics(
    daily_returns: np.ndarray,
    weights: np.ndarray,
    risk_free_rate_annual: float,
) -> PortfolioMetrics:
    return PortfolioMetrics(
        annualized_return=annualized_return(daily_returns),
        annualized_volatility=annualized_volatility(daily_returns),
        sharpe_ratio=sharpe_ratio(daily_returns, risk_free_rate_annual),
        sortino_ratio=sortino_ratio(daily_returns, risk_free_rate_annual),
        max_drawdown=max_drawdown(daily_returns),
        calmar_ratio=calmar_ratio(daily_returns),
        herfindahl_index=herfindahl_index(weights),
    )
