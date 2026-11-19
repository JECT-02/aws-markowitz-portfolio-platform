from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from common.config import StrategyConfig, UniverseConfig
from common.errors import ConfigurationError
from common.models import DataSource, IngestResult, RunMetadata
from ingest.handler import IngestDependencies, run_ingestion


class _FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


class _FakeCalendar:
    def __init__(self, is_open: bool) -> None:
        self._is_open = is_open
        self.checked: list[date] = []

    def is_trading_day(self, day: date) -> bool:
        self.checked.append(day)
        return self._is_open

    def trading_days(self, start: date, end: date) -> list[object]:
        raise NotImplementedError


class _RecordingService:
    def __init__(self, result: IngestResult) -> None:
        self.result = result
        self.calls: list[tuple[RunMetadata, list[str], date, date]] = []

    def run(
        self,
        metadata: RunMetadata,
        symbols: list[str],
        start: date,
        end: date,
    ) -> IngestResult:
        self.calls.append((metadata, symbols, start, end))
        return self.result


def _result() -> IngestResult:
    metadata = RunMetadata(
        run_id="run-1",
        run_date=date(2024, 1, 2),
        code_version="abc",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 2, 22, 0, tzinfo=UTC),
    )
    return IngestResult(metadata=metadata, accepted={"SPY": "key"}, quarantined={})


def _deps(
    service: _RecordingService,
    calendar: _FakeCalendar,
    clock: _FixedClock,
    strategy: StrategyConfig,
) -> IngestDependencies:
    return IngestDependencies(
        service=service,
        calendar=calendar,
        clock=clock,
        universe=UniverseConfig(tickers=["SPY"], max_weight=0.20),
        strategy=strategy,
        code_version="abc",
    )


def test_open_market_runs_ingestion_with_lookback(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 2, 22, 0, tzinfo=UTC))
    service = _RecordingService(_result())
    deps = _deps(service, _FakeCalendar(True), clock, make_strategy_config())
    outcome = run_ingestion({}, deps)
    # Contract with the pipeline state machine ResultSelector
    # (infra/modules/pipeline/state_machine.tf, output.stage_output_contract).
    assert set(outcome) == {"status", "run_id", "run_date", "source", "accepted", "quarantined"}
    assert outcome["status"] == "COMPLETED"
    assert outcome["run_date"] == "2024-01-02"
    assert outcome["accepted"] == {"SPY": "key"}
    metadata, symbols, start, end = service.calls[0]
    assert symbols == ["SPY"]
    assert start == date(2024, 1, 2) - timedelta(days=1000)
    assert end == date(2024, 1, 1)
    assert metadata.run_date == date(2024, 1, 2)


def test_closed_market_skips_ingestion(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 6, 22, 0, tzinfo=UTC))
    service = _RecordingService(_result())
    deps = _deps(service, _FakeCalendar(False), clock, make_strategy_config())
    outcome = run_ingestion({}, deps)
    # Contract with the pipeline state machine ResultSelector: the skipped
    # path must publish the same keys a completed run publishes.
    assert set(outcome) == {"status", "run_id", "run_date", "source", "accepted", "reason"}
    assert outcome["status"] == "SKIPPED"
    assert outcome["reason"] == "market_closed"
    assert service.calls == []


def test_skipped_contract_matches_the_state_machine_keys(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 6, 22, 0, tzinfo=UTC))
    deps = _deps(_RecordingService(_result()), _FakeCalendar(False), clock, make_strategy_config())
    outcome = run_ingestion({"source": "yfinance"}, deps)
    completed = {"status", "run_id", "run_date", "source", "accepted"}
    assert completed.issubset(outcome)
    assert outcome["source"] == "yfinance"
    assert outcome["accepted"] == {}


def test_event_overrides_run_date_and_run_id(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 2, 22, 0, tzinfo=UTC))
    service = _RecordingService(_result())
    deps = _deps(service, _FakeCalendar(True), clock, make_strategy_config())
    run_ingestion({"run_date": "2023-12-29", "run_id": "run-x"}, deps)
    metadata, _, _, _ = service.calls[0]
    assert metadata.run_date == date(2023, 12, 29)
    assert metadata.run_id == "run-x"


def test_mixed_is_an_artifact_label_and_never_an_event_source(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 2, 22, 0, tzinfo=UTC))
    service = _RecordingService(_result())
    deps = _deps(service, _FakeCalendar(True), clock, make_strategy_config())
    with pytest.raises(ConfigurationError, match="mixed"):
        run_ingestion({"source": "mixed"}, deps)


def test_invalid_run_date_is_rejected(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 2, 22, 0, tzinfo=UTC))
    service = _RecordingService(_result())
    deps = _deps(service, _FakeCalendar(True), clock, make_strategy_config())
    with pytest.raises(ConfigurationError):
        run_ingestion({"run_date": "02/01/2024"}, deps)


def test_invalid_run_id_is_rejected(
    make_strategy_config: Callable[..., StrategyConfig],
) -> None:
    clock = _FixedClock(datetime(2024, 1, 2, 22, 0, tzinfo=UTC))
    service = _RecordingService(_result())
    deps = _deps(service, _FakeCalendar(True), clock, make_strategy_config())
    with pytest.raises(ConfigurationError):
        run_ingestion({"run_id": "   "}, deps)
