from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from backtest.core.artifacts import S3BacktestWriter
from backtest.core.models import BacktestReport
from backtest.core.sources import resolve_report_source
from backtest.core.validation import TemporalValidation, ValidationResult
from common.clock import Clock, SystemClock
from common.config import (
    RuntimeSettings,
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_universe_config,
)
from common.env import load_env_file
from common.events import resolve_run_date, resolve_run_id
from common.logging import configure_logging, get_logger
from common.models import RunMetadata
from common.storage import S3Storage
from optimizer.core.repository import ReturnsMatrix, SilverReturnsRepository

logger = get_logger(__name__)


class BacktestData(Protocol):
    """Loads aligned silver log returns."""

    def load(
        self,
        symbols: list[str],
        window_days: int,
        max_days: int | None = None,
        run_date: date | None = None,
    ) -> ReturnsMatrix: ...


class BacktestValidation(Protocol):
    """Runs the temporal holdout validation (single level, see N-10)."""

    def run(self, matrix: ReturnsMatrix) -> ValidationResult: ...


@dataclass(frozen=True)
class BacktestDependencies:
    repository: BacktestData
    validation: BacktestValidation
    writer: S3BacktestWriter
    universe: UniverseConfig
    strategy: StrategyConfig
    code_version: str
    clock: Clock


def run_backtest(
    event: Mapping[str, object],
    deps: BacktestDependencies,
) -> dict[str, object]:
    """Run the weekly temporal validation and persist the gold report."""
    required_history = max(
        max(deps.strategy.validation_windows) * 2,
        deps.strategy.window_days,
    )
    run_date = resolve_run_date(event, deps.clock)
    matrix = deps.repository.load(
        deps.universe.tickers,
        window_days=required_history,
        max_days=deps.strategy.ingest_lookback_days,
        # Inclusive cutoff (ADR 0002): a weekly rerun never reads silver
        # partitions newer than the report's run date (mistakes.md QA-013).
        run_date=run_date,
    )
    metadata = RunMetadata(
        run_id=resolve_run_id(event),
        run_date=run_date,
        code_version=deps.code_version,
        # The report is attributed to the source(s) actually recorded in the
        # consumed silver partitions, never to a hardcoded default (7.3).
        source=resolve_report_source(matrix.sources),
        fetched_at=deps.clock.now(),
    )
    result = deps.validation.run(matrix)
    report = BacktestReport.from_validation_result(result, metadata, deps.clock.now())
    deps.writer.write(report)
    logger.info(
        "weekly backtest completed",
        extra={
            "run_id": metadata.run_id,
            "run_date": metadata.run_date.isoformat(),
            "selected_window_days": report.selected.window_days,
            "candidate_count": len(report.candidates),
        },
    )
    return _response(report)


def handler(event: Mapping[str, object], _context: object | None = None) -> dict[str, object]:
    """AWS Lambda entry point for the weekly backtest."""
    load_env_file(Path(".env"))
    configure_logging()
    return run_backtest(event, build_dependencies())


def build_dependencies() -> BacktestDependencies:
    runtime = RuntimeSettings.from_environment()
    storage_settings = StorageSettings.from_environment()
    universe = load_universe_config(runtime.config_dir / "universe.yaml")
    strategy = StrategyConfig.from_environment()
    storage = S3Storage(region_name=runtime.aws_region)
    return BacktestDependencies(
        repository=SilverReturnsRepository(storage, storage_settings.silver_bucket),
        validation=TemporalValidation(universe, strategy),
        writer=S3BacktestWriter(storage, storage_settings.gold_bucket),
        universe=universe,
        strategy=strategy,
        code_version=runtime.code_version,
        clock=SystemClock(),
    )


def _response(report: BacktestReport) -> dict[str, object]:
    return {
        "run_id": report.metadata.run_id,
        "run_date": report.metadata.run_date.isoformat(),
        "code_version": report.metadata.code_version,
        "selected": {
            "gamma": report.selected.gamma,
            "transaction_cost_lambda": report.selected.transaction_cost_lambda,
            "alpha": report.selected.alpha,
            "window_days": report.selected.window_days,
            "validation_sharpe": report.selected.validation_sharpe,
        },
        "out_of_sample": {
            mode: metrics.model_dump() for mode, metrics in report.out_of_sample.items()
        },
    }
