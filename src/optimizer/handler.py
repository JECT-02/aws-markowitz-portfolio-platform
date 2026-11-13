from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.clock import Clock, SystemClock
from common.config import (
    RuntimeSettings,
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_universe_config,
)
from common.env import load_env_file
from common.errors import ConfigurationError, InfeasibleError, SolverError
from common.events import resolve_run_date, resolve_run_id
from common.logging import configure_logging, get_logger
from common.models import DataSource, RunMetadata
from common.storage import S3Storage
from optimizer.core.models import Proposal
from optimizer.core.repository import SilverReturnsRepository
from optimizer.core.service import OptimizerService
from optimizer.core.state_adapter import (
    DynamoPortfolioState,
    PortfolioState,
    proposal_identifier,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class OptimizerDependencies:
    service: OptimizerRunner
    clock: Clock
    universe: UniverseConfig
    strategy: StrategyConfig
    code_version: str
    state: PortfolioState | None = None


class OptimizerRunner(Protocol):
    """Runs an optimization for one pipeline metadata record."""

    def run(
        self,
        metadata: RunMetadata,
        actual_weights: Mapping[str, float] | None = None,
    ) -> Proposal: ...


def run_optimizer(
    event: Mapping[str, object],
    deps: OptimizerDependencies,
) -> dict[str, object]:
    """Run the daily optimization for the event run date."""
    run_date = resolve_run_date(event, deps.clock)
    metadata = RunMetadata(
        run_id=resolve_run_id(event),
        run_date=run_date,
        code_version=deps.code_version,
        source=_resolve_source(event),
        fetched_at=deps.clock.now(),
    )
    actual_weights = _resolve_actual_weights(event)
    if actual_weights is None and deps.state is not None:
        actual_weights = deps.state.read_actual_weights()
    try:
        proposal = deps.service.run(metadata, actual_weights)
    except (SolverError, InfeasibleError) as exc:
        if deps.state is not None:
            deps.state.write_failure(proposal_identifier(metadata), metadata, str(exc))
        raise
    if deps.state is not None:
        deps.state.write_proposal(proposal)
    logger.info(
        "optimizer proposal ready",
        extra={
            "proposal_id": proposal.proposal_id,
            "run_id": metadata.run_id,
            "status": proposal.status,
        },
    )
    return _response(proposal)


def handler(event: Mapping[str, object], _context: object | None = None) -> dict[str, object]:
    """AWS Lambda entry point for the optimizer stage."""
    load_env_file(Path(".env"))
    configure_logging()
    return run_optimizer(event, build_dependencies())


def build_dependencies() -> OptimizerDependencies:
    runtime = RuntimeSettings.from_environment()
    storage_settings = StorageSettings.from_environment()
    universe = load_universe_config(runtime.config_dir / "universe.yaml")
    strategy = StrategyConfig.from_environment()
    storage = S3Storage(region_name=runtime.aws_region)
    service = OptimizerService(
        repository=SilverReturnsRepository(storage, storage_settings.silver_bucket),
        storage=storage,
        gold_bucket=storage_settings.gold_bucket,
        universe=universe,
        strategy=strategy,
        clock=SystemClock(),
    )
    return OptimizerDependencies(
        service=service,
        clock=SystemClock(),
        universe=universe,
        strategy=strategy,
        code_version=runtime.code_version,
        state=_build_state(runtime),
    )


def _build_state(runtime: RuntimeSettings) -> PortfolioState | None:
    table_name = os.environ.get("STATE_TABLE")
    if table_name is None or not table_name.strip():
        logger.warning("STATE_TABLE not set, portfolio state will not be persisted")
        return None
    return DynamoPortfolioState(table_name.strip(), region_name=runtime.aws_region)


def _response(proposal: Proposal) -> dict[str, object]:
    return {
        "status": proposal.status,
        "proposal_id": proposal.proposal_id,
        "run_id": proposal.metadata.run_id,
        "run_date": proposal.run_date.isoformat(),
        "weights": dict(proposal.weights),
        "drift_l1": proposal.drift_l1,
        "rebalance_required": proposal.rebalance_required,
        "excluded": dict(proposal.excluded),
    }


def _resolve_actual_weights(event: Mapping[str, object]) -> dict[str, float] | None:
    value = event.get("actual_weights")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigurationError("actual_weights must be an object")
    weights: dict[str, float] = {}
    for symbol, raw in value.items():
        if not isinstance(symbol, str):
            raise ConfigurationError("actual_weights keys must be symbols")
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise ConfigurationError("actual_weights values must be numbers")
        weights[symbol] = float(raw)
    return weights


def _resolve_source(event: Mapping[str, object]) -> DataSource:
    value = event.get("source", DataSource.ALPACA.value)
    if not isinstance(value, str):
        raise ConfigurationError("source must be a string")
    try:
        return DataSource(value)
    except ValueError as exc:
        raise ConfigurationError(f"unsupported source: {value}") from exc
