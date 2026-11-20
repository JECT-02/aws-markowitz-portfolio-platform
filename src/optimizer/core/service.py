from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from common.clock import Clock
from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError, InfeasibleError, StorageError
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
from optimizer.core.models import BaselineSource, PortfolioMetrics, Proposal
from optimizer.core.problem import INACCURATE_STATUS, INACCURATE_TOLERANCE, MarkowitzOptimizer
from optimizer.core.repository import PricesRepository, ReturnsMatrix, ReturnsRepository

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


class ProposalStatusReader(Protocol):
    """Reads the execution status of a proposal from the state table."""

    def read_proposal_status(self, proposal_id: str) -> str | None: ...


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
        prices: PricesRepository,
        proposal_status: ProposalStatusReader | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._gold_bucket = gold_bucket
        self._universe = universe
        self._strategy = strategy
        self._clock = clock
        self._prices = prices
        self._proposal_status = proposal_status

    def run(
        self,
        metadata: RunMetadata,
        actual_weights: Mapping[str, float] | None = None,
    ) -> Proposal:
        """Optimize the portfolio and persist the resulting proposal."""
        matrix = self._repository.load(
            self._universe.tickers,
            self._strategy.window_days,
            run_date=metadata.run_date,
        )
        if matrix.n_assets < MIN_ASSETS:
            raise InfeasibleError(
                f"only {matrix.n_assets} assets have sufficient history; {MIN_ASSETS} required"
            )
        reference_prices = self._prices.load_reference_prices(
            metadata.run_date,
            metadata.source,
        )
        missing_prices = [symbol for symbol in matrix.symbols if symbol not in reference_prices]
        if missing_prices:
            raise DataValidationError(
                "missing reference prices for symbols: " + ", ".join(missing_prices)
            )
        estimates = estimate(matrix.values, self._strategy.return_shrinkage_alpha)
        if actual_weights is None:
            # AGENTS.md section 5.3: without reconciled state the drift is
            # measured against a synthetic 1/N baseline; the proposal carries
            # baseline_source so it is never approved as the real portfolio.
            logger.warning(
                "no reconciled state; proposal baseline is the 1/N fallback",
                extra={"run_id": metadata.run_id},
            )
            baseline_source: BaselineSource = "equal_weight_fallback"
        else:
            baseline_source = "state"
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
                    "tolerance": INACCURATE_TOLERANCE,
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
            baseline_source=baseline_source,
            drift_l1=drift,
            benchmark_weights=benchmarks.as_mapping(matrix.symbols),
            metrics=metrics,
            covariance_shrinkage=estimates.shrinkage,
            window_days=self._strategy.window_days,
            prices_ref=(f"{matrix.dates[0].isoformat()}..{matrix.dates[-1].isoformat()}"),
            excluded=matrix.excluded,
            run_date=metadata.run_date,
        )
        self._write(proposal, reference_prices)
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

    def _write(self, proposal: Proposal, reference_prices: Mapping[str, float]) -> None:
        # The run-partition artifact is replaced only while the proposal it
        # holds is unexecuted (see _write_correctable); the latest pointer
        # refreshes unconditionally.
        payload = proposal.model_dump(mode="json")
        key = f"run_date={proposal.run_date.isoformat()}/proposal.json"
        self._write_correctable(self._gold_bucket, key, payload, proposal)
        self._storage.write_json(self._gold_bucket, "latest.json", payload)
        prices_key = f"run_date={proposal.run_date.isoformat()}/prices.json"
        self._storage.write_json_if_absent(
            self._gold_bucket,
            prices_key,
            {
                "prices": {symbol: price for symbol, price in sorted(reference_prices.items())},
            },
        )

    def _write_correctable(
        self,
        bucket: str,
        key: str,
        payload: dict[str, object],
        proposal: Proposal,
    ) -> None:
        """Create the artifact, or overwrite it while its proposal is unexecuted.

        ``PENDING``/``NO_REBALANCE`` proposals are the correctable set (N-22);
        EXECUTING/EXECUTED/PARTIAL/FAILED artifacts are final.
        """
        if not self._storage.exists(bucket, key):
            self._storage.write_json_if_absent(bucket, key, payload)
            return
        try:
            existing = self._storage.read_json(bucket, key)
        except StorageError:
            logger.warning(
                "unreadable gold artifact stays untouched",
                extra={"key": key},
            )
            return
        if existing.get("status") not in {"PENDING", "NO_REBALANCE"}:
            logger.warning(
                "gold artifact holds a final proposal and stays untouched",
                extra={"key": key},
            )
            return
        previous_id = existing.get("proposal_id")
        if previous_id == proposal.proposal_id:
            # A rerun of the same proposal corrects its stale version (N-22).
            self._storage.write_json(bucket, key, payload)
            return
        # A same-day rerun has a new proposal id (it embeds the run id), so the
        # dated artifact would stay stale forever. Replace the earlier proposal
        # only if it was never executed. The artifact's own status cannot say
        # so, because the executor records execution only in the state table,
        # so that table decides; without it the artifact is kept (fail closed).
        if self._proposal_status is None or not isinstance(previous_id, str):
            logger.warning(
                "cannot verify the earlier proposal; gold artifact stays untouched",
                extra={"key": key, "previous_proposal_id": str(previous_id)},
            )
            return
        previous_status = self._proposal_status.read_proposal_status(previous_id)
        if previous_status not in {"PENDING", "NO_REBALANCE"}:
            logger.warning(
                "earlier proposal is final in the state table; gold artifact stays untouched",
                extra={
                    "key": key,
                    "previous_proposal_id": previous_id,
                    "previous_status": str(previous_status),
                },
            )
            return
        self._storage.write_json(bucket, key, payload)


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
    # Weights are fractions of the account equity; uninvested cash is the
    # remainder, so the sum may be below one but never above it.
    if float(weights.sum()) > 1.0 + WEIGHT_SUM_TOLERANCE:
        raise DataValidationError("actual weights must not exceed the account equity")
    return weights


def _as_weight_mapping(symbols: list[str], weights: np.ndarray) -> dict[str, float]:
    return {symbol: float(weight) for symbol, weight in zip(symbols, weights, strict=True)}
