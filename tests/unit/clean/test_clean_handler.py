from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from clean.handler import CleanDependencies, run_clean
from common.config import StrategyConfig, UniverseConfig
from common.errors import ConfigurationError
from common.models import CleanResult, DataSource, RunMetadata


class _FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


class _RecordingService:
    def __init__(self, result: CleanResult) -> None:
        self.result = result
        self.calls: list[tuple[RunMetadata, list[str], int]] = []

    def run(
        self,
        metadata: RunMetadata,
        symbols: list[str],
        max_forward_fill_days: int,
    ) -> CleanResult:
        self.calls.append((metadata, symbols, max_forward_fill_days))
        return self.result


def _result() -> CleanResult:
    metadata = RunMetadata(
        run_id="run-1",
        run_date=date(2024, 1, 2),
        code_version="abc",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 2, 22, 0, tzinfo=UTC),
    )
    return CleanResult(
        metadata=metadata,
        used=["SPY"],
        missing=[],
        excluded={},
        partitions=["year=2024/month=01/returns.parquet"],
    )


def _deps(service: _RecordingService, strategy: StrategyConfig) -> CleanDependencies:
    return CleanDependencies(
        service=service,
        clock=_FixedClock(datetime(2024, 1, 2, 22, 0, tzinfo=UTC)),
        universe=UniverseConfig(tickers=["SPY"], max_weight=0.20),
        strategy=strategy,
        code_version="abc",
    )


def test_run_clean_returns_summary(make_strategy_config: Callable[..., StrategyConfig]) -> None:
    service = _RecordingService(_result())
    outcome = run_clean({}, _deps(service, make_strategy_config()))
    assert outcome["status"] == "COMPLETED"
    assert outcome["source"] == "alpaca"
    assert outcome["partitions"] == ["year=2024/month=01/returns.parquet"]
    _, symbols, fill_days = service.calls[0]
    assert symbols == ["SPY"]
    assert fill_days == 2


def test_event_source_is_used(make_strategy_config: Callable[..., StrategyConfig]) -> None:
    service = _RecordingService(_result())
    run_clean({"source": "yfinance"}, _deps(service, make_strategy_config()))
    metadata, _, _ = service.calls[0]
    assert metadata.source is DataSource.YFINANCE


def test_invalid_source_is_rejected(make_strategy_config: Callable[..., StrategyConfig]) -> None:
    service = _RecordingService(_result())
    with pytest.raises(ConfigurationError):
        run_clean({"source": "nasdaq"}, _deps(service, make_strategy_config()))
