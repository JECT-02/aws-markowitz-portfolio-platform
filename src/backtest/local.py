from __future__ import annotations

import json
from pathlib import Path

from backtest.core.models import BacktestReport
from backtest.core.sources import resolve_report_source
from backtest.core.validation import TemporalValidation
from common.clock import SystemClock
from common.config import (
    RuntimeSettings,
    StorageSettings,
    StrategyConfig,
    load_universe_config,
)
from common.env import load_env_file
from common.logging import configure_logging
from common.models import RunMetadata
from common.storage import S3Storage
from optimizer.core.repository import SilverReturnsRepository


def main() -> int:
    """Run temporal validation and a held-out backtest against silver data."""
    load_env_file(Path(".env"))
    configure_logging()
    runtime = RuntimeSettings.from_environment()
    storage_settings = StorageSettings.from_environment()
    universe = load_universe_config(runtime.config_dir / "universe.yaml")
    strategy = StrategyConfig.from_environment()
    repository = SilverReturnsRepository(
        S3Storage(region_name=runtime.aws_region), storage_settings.silver_bucket
    )
    required_history = max(
        max(strategy.validation_windows) * 2,
        strategy.window_days,
    )
    matrix = repository.load(
        universe.tickers,
        window_days=required_history,
        max_days=strategy.ingest_lookback_days,
    )
    result = TemporalValidation(universe, strategy).run(matrix)
    clock = SystemClock()
    metadata = RunMetadata(
        run_id=f"local-{matrix.dates[-1].isoformat()}",
        run_date=matrix.dates[-1],
        code_version=runtime.code_version,
        source=resolve_report_source(matrix.sources),
        fetched_at=clock.now(),
    )
    report = BacktestReport.from_validation_result(result, metadata, clock.now())
    print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
