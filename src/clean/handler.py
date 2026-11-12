from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from clean.core.service import CleanService
from common.clock import Clock, SystemClock
from common.config import (
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_strategy_config,
    load_universe_config,
)
from common.errors import ConfigurationError
from common.events import resolve_run_date, resolve_run_id
from common.logging import configure_logging, get_logger
from common.models import DataSource, RunMetadata
from common.storage import S3Storage

logger = get_logger(__name__)


@dataclass(frozen=True)
class CleanDependencies:
    service: CleanService
    clock: Clock
    universe: UniverseConfig
    strategy: StrategyConfig
    code_version: str


def run_clean(event: Mapping[str, object], deps: CleanDependencies) -> dict[str, object]:
    """Run silver cleaning for the event run date."""
    run_date = resolve_run_date(event, deps.clock)
    metadata = RunMetadata(
        run_id=resolve_run_id(event),
        run_date=run_date,
        code_version=deps.code_version,
        source=_resolve_source(event),
        fetched_at=deps.clock.now(),
    )
    result = deps.service.run(
        metadata,
        deps.universe.tickers,
        deps.strategy.require_forward_fill_days(),
    )
    logger.info(
        "clean completed",
        extra={
            "run_id": metadata.run_id,
            "run_date": run_date.isoformat(),
            "used": len(result.used),
            "missing": len(result.missing),
            "excluded": len(result.excluded),
        },
    )
    return {
        "status": "COMPLETED",
        "run_id": metadata.run_id,
        "run_date": run_date.isoformat(),
        "source": metadata.source.value,
        "used": list(result.used),
        "missing": list(result.missing),
        "excluded": dict(result.excluded),
        "partitions": list(result.partitions),
    }


def handler(event: Mapping[str, object], _context: object | None = None) -> dict[str, object]:
    """AWS Lambda entry point for the clean stage."""
    configure_logging()
    return run_clean(event, _build_dependencies())


def _build_dependencies() -> CleanDependencies:
    storage_settings = StorageSettings.from_environment()
    config_dir = Path(os.environ.get("CONFIG_DIR", "config"))
    universe = load_universe_config(config_dir / "universe.yaml")
    strategy = load_strategy_config(config_dir / "strategy.yaml")
    service = CleanService(
        storage=S3Storage(),
        bronze_bucket=storage_settings.bronze_bucket,
        silver_bucket=storage_settings.silver_bucket,
    )
    return CleanDependencies(
        service=service,
        clock=SystemClock(),
        universe=universe,
        strategy=strategy,
        code_version=os.environ.get("CODE_VERSION", "unknown"),
    )


def _resolve_source(event: Mapping[str, object]) -> DataSource:
    value = event.get("source", DataSource.ALPACA.value)
    if not isinstance(value, str):
        raise ConfigurationError("source must be a string")
    try:
        return DataSource(value)
    except ValueError as exc:
        raise ConfigurationError(f"unsupported source: {value}") from exc
