from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from backtest.core.validation import PeriodMetrics, TemporalValidation, ValidationResult
from common.config import (
    RuntimeSettings,
    StorageSettings,
    StrategyConfig,
    load_universe_config,
)
from common.env import load_env_file
from common.logging import configure_logging
from common.storage import S3Storage
from optimizer.core.repository import SilverReturnsRepository

MODES = ("optimizer", "equal_weight", "minimum_variance", "spy")


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
    print(json.dumps(_payload(result), indent=2, sort_keys=True))
    return 0


def _payload(result: ValidationResult) -> dict[str, object]:
    return {
        "selected": asdict(result.selected),
        "comparison": _comparison(result),
        "sensitivity": {
            str(window): asdict(metrics) for window, metrics in sorted(result.sensitivity.items())
        },
        "in_sample": {mode: asdict(metrics) for mode, metrics in result.in_sample_metrics.items()},
        "out_of_sample": {
            mode: asdict(metrics) for mode, metrics in result.out_of_sample_metrics.items()
        },
        "candidates": [asdict(candidate) for candidate in result.candidates],
    }


def _comparison(result: ValidationResult) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for mode in MODES:
        in_sample = result.in_sample_metrics.get(mode)
        out_of_sample = result.out_of_sample_metrics.get(mode)
        if in_sample is None and out_of_sample is None:
            continue
        rows.append(
            {
                "mode": mode,
                "in_sample_sharpe": _sharpe(in_sample),
                "out_of_sample_sharpe": _sharpe(out_of_sample),
                "out_of_sample_annualized_return": (
                    out_of_sample.annualized_return if out_of_sample is not None else None
                ),
                "out_of_sample_max_drawdown": (
                    out_of_sample.max_drawdown if out_of_sample is not None else None
                ),
            }
        )
    return rows


def _sharpe(metrics: PeriodMetrics | None) -> float | None:
    return metrics.sharpe_ratio if metrics is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
