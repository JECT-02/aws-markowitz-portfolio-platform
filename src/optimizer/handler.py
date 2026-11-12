from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.clock import Clock, SystemClock
from common.config import (
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_universe_config,
)
from common.env import config_directory, load_env_file
from common.errors import ConfigurationError
from common.events import resolve_run_date, resolve_run_id
from common.logging import configure_logging, get_logger
from common.models import DataSource, RunMetadata
from common.storage import S3Storage
from optimizer.core.models import Proposal
from optimizer.core.repository import SilverReturnsRepository
from optimizer.core.service import OptimizerService

logger = get_logger(__name__)


@dataclass(frozen=True)
class OptimizerDependencies:
    service: OptimizerRunner
    clock: Clock
    universe: UniverseConfig
    strategy: StrategyConfig
    code_version: str


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
    proposal = deps.service.run(metadata, _resolve_actual_weights(event))
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
    storage_settings = StorageSettings.from_environment()
    universe = load_universe_config(config_directory() / "universe.yaml")
    strategy = StrategyConfig.from_environment()
    storage = S3Storage()
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
        code_version=os.environ.get("CODE_VERSION", "unknown"),
    )


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
