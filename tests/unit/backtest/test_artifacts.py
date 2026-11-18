from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from pydantic import ValidationError

from backtest.core.artifacts import (
    BACKTEST_KEY_TEMPLATE,
    BACKTEST_LATEST_KEY,
    S3BacktestWriter,
)
from backtest.core.models import BacktestPeriodMetrics, BacktestReport
from backtest.core.validation import (
    ParameterCandidate,
    PeriodMetrics,
    ValidationResult,
)
from common.models import DataSource, RunMetadata
from common.storage import S3Storage

GOLD = "gold-test"


def _period(sharpe: float) -> PeriodMetrics:
    return PeriodMetrics(
        annualized_return=0.08,
        annualized_volatility=0.12,
        sharpe_ratio=sharpe,
        max_drawdown=-0.15,
    )


def _candidate(
    gamma: float = 4.0,
    window: int = 126,
    sharpe: float = 0.8,
) -> ParameterCandidate:
    return ParameterCandidate(
        gamma=gamma,
        transaction_cost_lambda=0.001,
        alpha=0.5,
        window_days=window,
        validation_sharpe=sharpe,
        fold_metrics=[_period(0.7), _period(sharpe)],
    )


def _result() -> ValidationResult:
    return ValidationResult(
        selected=_candidate(),
        candidates=[_candidate(), _candidate(gamma=8.0, window=63, sharpe=0.6)],
        sensitivity={126: _period(0.7), 63: _period(0.5)},
        validation_metrics={"optimizer": _period(0.75)},
        periods={
            "train": (date(2025, 8, 4), date(2026, 2, 2)),
            "validation": (date(2026, 2, 2), date(2026, 2, 7)),
            "test": (date(2026, 2, 7), date(2026, 2, 7)),
        },
        in_sample_metrics={"optimizer": _period(1.1), "spy": _period(0.9)},
        out_of_sample_metrics={"optimizer": _period(0.8)},
    )


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-1",
        run_date=date(2026, 2, 7),
        code_version="abc123",
        source=DataSource.ALPACA,
        fetched_at=datetime(2026, 2, 7, 13, 0, tzinfo=UTC),
    )


def _report() -> BacktestReport:
    return BacktestReport.from_validation_result(
        _result(),
        _metadata(),
        datetime(2026, 2, 7, 13, 0, tzinfo=UTC),
    )


def test_report_carries_the_validation_outcome() -> None:
    report = _report()
    assert report.selected.gamma == 4.0
    assert report.selected.window_days == 126
    assert report.selected.validation_sharpe == pytest.approx(0.8)
    assert len(report.candidates) == 2
    assert report.sensitivity == {"126": _pydantic_period(0.7), "63": _pydantic_period(0.5)}
    assert report.validation == {"optimizer": _pydantic_period(0.75)}
    assert report.periods["train"].start == date(2025, 8, 4)
    assert report.periods["validation"].end == date(2026, 2, 7)
    assert report.periods["test"].start == date(2026, 2, 7)
    assert report.in_sample == {"optimizer": _pydantic_period(1.1), "spy": _pydantic_period(0.9)}
    assert report.out_of_sample == {"optimizer": _pydantic_period(0.8)}


def _pydantic_period(sharpe: float) -> BacktestPeriodMetrics:
    return BacktestPeriodMetrics(
        annualized_return=0.08,
        annualized_volatility=0.12,
        sharpe_ratio=sharpe,
        max_drawdown=-0.15,
    )


def test_comparison_covers_every_mode_with_metrics_in_order() -> None:
    report = _report()
    modes = [row.mode for row in report.comparison]
    assert modes == ["optimizer", "spy"]
    optimizer_row = report.comparison[0]
    assert optimizer_row.in_sample_sharpe == pytest.approx(1.1)
    assert optimizer_row.out_of_sample_sharpe == pytest.approx(0.8)
    assert optimizer_row.out_of_sample_max_drawdown == pytest.approx(-0.15)
    spy_row = report.comparison[1]
    assert spy_row.out_of_sample_sharpe is None
    assert spy_row.out_of_sample_annualized_return is None


def test_report_is_deterministic_and_json_round_trippable() -> None:
    first = _report()
    second = BacktestReport.from_validation_result(
        _result(),
        _metadata(),
        datetime(2026, 2, 7, 13, 0, tzinfo=UTC),
    )
    assert first == second
    payload = first.model_dump(mode="json")
    assert BacktestReport.model_validate(json.loads(json.dumps(payload))) == first


def test_report_carries_the_run_metadata() -> None:
    report = _report()
    assert report.metadata.run_id == "run-1"
    assert report.metadata.code_version == "abc123"
    assert report.metadata.source == DataSource.ALPACA
    assert report.generated_at == datetime(2026, 2, 7, 13, 0, tzinfo=UTC)


def test_report_rejects_non_finite_metrics() -> None:
    with pytest.raises(ValidationError):
        BacktestPeriodMetrics(
            annualized_return=float("nan"),
            annualized_volatility=0.12,
            sharpe_ratio=1.0,
            max_drawdown=-0.15,
        )


def test_report_matches_the_shared_gold_fixture() -> None:
    """Pin the artifact schema: the reader validates exactly this payload."""
    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "backtest_report.json"
    assert _report().model_dump(mode="json") == json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture
def writer() -> Iterator[S3BacktestWriter]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=GOLD)
        yield S3BacktestWriter(S3Storage(client=boto3.client("s3", region_name="us-east-1")), GOLD)


def _read(key: str) -> dict[str, object]:
    body = boto3.client("s3", region_name="us-east-1").get_object(Bucket=GOLD, Key=key)["Body"]
    return json.loads(body.read())


def test_writer_publishes_the_partition_and_the_latest_pointer(writer: S3BacktestWriter) -> None:
    writer.write(_report())
    partition_key = BACKTEST_KEY_TEMPLATE.format(run_date="2026-02-07")
    expected = _report().model_dump(mode="json")
    assert _read(partition_key) == expected
    assert _read(BACKTEST_LATEST_KEY) == expected


def test_writer_rerun_keeps_the_partition_and_refreshes_the_pointer(
    writer: S3BacktestWriter,
) -> None:
    writer.write(_report())
    rerun = BacktestReport.from_validation_result(
        _result(),
        RunMetadata(
            run_id="run-2",
            run_date=date(2026, 2, 7),
            code_version="def456",
            source=DataSource.ALPACA,
            fetched_at=datetime(2026, 2, 7, 14, 0, tzinfo=UTC),
        ),
        datetime(2026, 2, 7, 14, 0, tzinfo=UTC),
    )
    writer.write(rerun)
    partition_key = BACKTEST_KEY_TEMPLATE.format(run_date="2026-02-07")
    assert _read(partition_key) == _report().model_dump(mode="json")
    assert _read(BACKTEST_LATEST_KEY) == rerun.model_dump(mode="json")
