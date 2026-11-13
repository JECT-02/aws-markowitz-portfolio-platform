from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from common.clock import Clock
from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError, InfeasibleError
from common.logging import get_logger
from common.models import RunMetadata
from common.storage import ObjectStorage
from optimizer.core.benchmarks import equal_weights, minimum_variance, spy_weights
from optimizer.core.estimation import estimate
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
from optimizer.core.models import PortfolioMetrics, Proposal
from optimizer.core.problem import INACCURATE_STATUS, MarkowitzOptimizer
from optimizer.core.repository import ReturnsMatrix, ReturnsRepository

logger = get_logger(__name__)

WEIGHT_SUM_TOLERANCE = 1e-6
MIN_ASSETS = 5


@dataclass(frozen=True)
class BenchmarkAllocations:
    equal_weight: np.ndarray
    minimum_variance: np.ndarray
    spy: np.ndarray

    def as_mapping(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        return {
            "equal_weight": _as_weight_mapping(symbols, self.equal_weight),
            "minimum_variance": _as_weight_mapping(symbols, self.minimum_variance),
            "spy": _as_weight_mapping(symbols, self.spy),
        }


class OptimizerService:
    """Runs the daily optimization and writes a proposal to the gold layer."""

    def __init__(
        self,
        repository: ReturnsRepository,
        storage: ObjectStorage,
        gold_bucket: str,
        universe: UniverseConfig,
        strategy: StrategyConfig,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._gold_bucket = gold_bucket
        self._universe = universe
        self._strategy = strategy
        self._clock = clock

    def run(
        self,
        metadata: RunMetadata,
        actual_weights: Mapping[str, float] | None = None,
    ) -> Proposal:
        """Optimize the portfolio and persist the resulting proposal."""
        matrix = self._repository.load(self._universe.tickers, self._strategy.window_days)
        if matrix.n_assets < MIN_ASSETS:
            raise InfeasibleError(
                f"only {matrix.n_assets} assets have sufficient history; {MIN_ASSETS} required"
            )
        estimates = estimate(matrix.values, self._strategy.return_shrinkage_alpha)
        if actual_weights is None:
            logger.warning(
                "actual weights not provided, defaulting to equal weights",
                extra={"run_id": metadata.run_id},
            )
        actual = _resolve_actual_weights(
            matrix.symbols,
            self._universe.max_weight,
            actual_weights,
        )
        optimizer = MarkowitzOptimizer(matrix.n_assets, self._universe.max_weight)
        result = optimizer.solve(
            estimates.covariance,
            estimates.mean,
            actual,
            gamma=self._strategy.risk_aversion,
            transaction_cost_lambda=self._strategy.transaction_cost_lambda,
        )
        if result.status == INACCURATE_STATUS:
            logger.warning(
                "solver returned optimal_inaccurate",
                extra={
                    "run_id": metadata.run_id,
                    "assets": matrix.n_assets,
                    "tolerance": 1e-4,
                },
            )
        benchmarks = BenchmarkAllocations(
            equal_weight=equal_weights(matrix.n_assets, self._universe.max_weight),
            minimum_variance=minimum_variance(estimates.covariance, self._universe.max_weight),
            spy=spy_weights(matrix.symbols),
        )
        allocations = {
            "optimizer": result.weights,
            "equal_weight": benchmarks.equal_weight,
            "minimum_variance": benchmarks.minimum_variance,
            "spy": benchmarks.spy,
        }
        metrics = {
            name: _metrics(matrix, weights, self._strategy.risk_free_rate_annual)
            for name, weights in allocations.items()
        }
        drift = turnover_l1(result.weights, actual)
        rebalance_required = drift > self._strategy.rebalance_l1_threshold
        proposal = Proposal(
            proposal_id=f"{metadata.run_date.isoformat()}-{metadata.run_id}",
            metadata=metadata,
            created_at=self._clock.now(),
            status="PENDING" if rebalance_required else "NO_REBALANCE",
            rebalance_required=rebalance_required,
            weights=_as_weight_mapping(matrix.symbols, result.weights),
            actual_weights=_as_weight_mapping(matrix.symbols, actual),
            drift_l1=drift,
            benchmark_weights=benchmarks.as_mapping(matrix.symbols),
            metrics=metrics,
            covariance_shrinkage=estimates.shrinkage,
            window_days=self._strategy.window_days,
            excluded=matrix.excluded,
            run_date=metadata.run_date,
        )
        self._write(proposal)
        logger.info(
            "optimizer completed",
            extra={
                "proposal_id": proposal.proposal_id,
                "assets": matrix.n_assets,
                "drift_l1": drift,
                "rebalance_required": rebalance_required,
            },
        )
        return proposal

    def _write(self, proposal: Proposal) -> None:
        payload = proposal.model_dump(mode="json")
        key = f"run_date={proposal.run_date.isoformat()}/proposal.json"
        self._storage.write_json(self._gold_bucket, key, payload)
        self._storage.write_json(self._gold_bucket, "latest.json", payload)


def _metrics(
    matrix: ReturnsMatrix,
    weights: np.ndarray,
    risk_free_rate_annual: float,
) -> PortfolioMetrics:
    portfolio_returns = portfolio_log_returns(matrix.values, weights)
    return PortfolioMetrics(
        annualized_return=annualized_return(portfolio_returns),
        annualized_volatility=annualized_volatility(portfolio_returns),
        sharpe_ratio=sharpe_ratio(portfolio_returns, risk_free_rate_annual),
        sortino_ratio=sortino_ratio(portfolio_returns, risk_free_rate_annual),
        max_drawdown=max_drawdown(portfolio_returns),
        calmar_ratio=calmar_ratio(portfolio_returns),
        herfindahl_index=herfindahl_index(weights),
    )


def _resolve_actual_weights(
    symbols: list[str],
    max_weight: float,
    provided: Mapping[str, float] | None,
) -> np.ndarray:
    if provided is None:
        return equal_weights(len(symbols), max_weight)
    normalized = {symbol.strip().upper(): float(value) for symbol, value in provided.items()}
    weights = np.array([normalized.get(symbol, 0.0) for symbol in symbols], dtype=float)
    if not np.isfinite(weights).all():
        raise DataValidationError("actual weights contain non-finite values")
    if float(weights.min()) < 0.0:
        raise DataValidationError("actual weights must be non-negative")
    if abs(float(weights.sum()) - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise DataValidationError("actual weights must sum to one")
    return weights


def _as_weight_mapping(symbols: list[str], weights: np.ndarray) -> dict[str, float]:
    return {symbol: float(weight) for symbol, weight in zip(symbols, weights, strict=True)}
