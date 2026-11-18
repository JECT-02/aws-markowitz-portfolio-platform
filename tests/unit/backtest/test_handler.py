from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from backtest.core.artifacts import BACKTEST_KEY_TEMPLATE, BACKTEST_LATEST_KEY, S3BacktestWriter
from backtest.core.validation import ParameterCandidate, PeriodMetrics, ValidationResult
from backtest.handler import BacktestDependencies, run_backtest
from common.config import StrategyConfig, UniverseConfig
from common.errors import ConfigurationError, DataValidationError
from common.storage import S3Storage
from optimizer.core.repository import ReturnsMatrix

GOLD = "gold-test"
SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "TLT", "GLD"]


@dataclass(frozen=True)
class _FakeClock:
    now_value: datetime

    def now(self) -> datetime:
        return self.now_value


class _FakeRepository:
    def __init__(self, matrix: ReturnsMatrix) -> None:
        self.matrix = matrix
        self.calls: list[tuple[list[str], int, int | None]] = []

    def load(
        self,
        symbols: list[str],
        window_days: int,
        max_days: int | None = None,
    ) -> ReturnsMatrix:
        self.calls.append((symbols, window_days, max_days))
        return self.matrix


class _FakeValidation:
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        self.runs = 0

    def run(self, _matrix: ReturnsMatrix) -> ValidationResult:
        self.runs += 1
        return self.result


class _FailingValidation:
    def run(self, _matrix: ReturnsMatrix) -> ValidationResult:
        raise DataValidationError("no parameter combination could be validated")


def _matrix() -> ReturnsMatrix:
    dates = [date(2026, 1, 5) + timedelta(days=offset) for offset in range(5)]
    return ReturnsMatrix(
        symbols=list(SYMBOLS),
        dates=dates,
        values=[[0.001] * len(SYMBOLS)] * 5,
        excluded={},
        sources=frozenset({"alpaca"}),
    )


def _period(sharpe: float) -> PeriodMetrics:
    return PeriodMetrics(
        annualized_return=0.08,
        annualized_volatility=0.12,
        sharpe_ratio=sharpe,
        max_drawdown=-0.15,
    )


def _candidate() -> ParameterCandidate:
    return ParameterCandidate(
        gamma=4.0,
        transaction_cost_lambda=0.001,
        alpha=0.5,
        window_days=126,
        validation_sharpe=0.8,
        fold_metrics=[_period(0.7), _period(0.9)],
    )


def _result() -> ValidationResult:
    return ValidationResult(
        selected=_candidate(),
        candidates=[_candidate()],
        sensitivity={126: _period(0.9)},
        validation_metrics={"optimizer": _period(0.75)},
        periods={
            "train": (date(2026, 1, 5), date(2026, 2, 2)),
            "validation": (date(2026, 2, 2), date(2026, 2, 9)),
            "test": (date(2026, 2, 9), date(2026, 2, 9)),
        },
        in_sample_metrics={"optimizer": _period(1.1), "spy": _period(0.9)},
        out_of_sample_metrics={"optimizer": _period(0.8)},
    )


def _strategy() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "risk_aversion": 4.0,
            "transaction_cost_lambda": 0.001,
            "return_shrinkage_alpha": 0.5,
            "risk_free_rate_annual": 0.04,
            "max_forward_fill_days": 2,
            "window_days": 252,
            "ingest_lookback_days": 1000,
            "rebalance_l1_threshold": 0.10,
            "validation_gammas": [1.0, 4.0],
            "validation_lambdas": [0.0, 0.001],
            "validation_alphas": [0.5],
            "validation_windows": [63, 126, 252],
            "validation_folds": 2,
        }
    )


def _universe() -> UniverseConfig:
    return UniverseConfig(tickers=SYMBOLS, max_weight=0.20)


def _deps(
    matrix: ReturnsMatrix,
    validation: object,
) -> tuple[BacktestDependencies, _FakeRepository]:
    repository = _FakeRepository(matrix)
    clock = _FakeClock(datetime(2026, 2, 9, 13, 0, tzinfo=UTC))
    deps = BacktestDependencies(
        repository=repository,
        validation=validation,  # type: ignore[arg-type]
        writer=S3BacktestWriter(
            S3Storage(client=boto3.client("s3", region_name="us-east-1")), GOLD
        ),
        universe=_universe(),
        strategy=_strategy(),
        code_version="abc123",
        clock=clock,
    )
    return deps, repository


def _gold_keys() -> list[str]:
    paginator = boto3.client("s3", region_name="us-east-1").get_paginator("list_objects_v2")
    return [
        item["Key"] for page in paginator.paginate(Bucket=GOLD) for item in page.get("Contents", [])
    ]


def _read(key: str) -> dict[str, object]:
    body = boto3.client("s3", region_name="us-east-1").get_object(Bucket=GOLD, Key=key)["Body"]
    return json.loads(body.read())


@pytest.fixture
def gold() -> Iterator[None]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=GOLD)
        yield


def test_backtest_loads_the_full_validation_history(gold: None) -> None:
    deps, repository = _deps(_matrix(), _FakeValidation(_result()))
    run_backtest({}, deps)  # type: ignore[arg-type]
    # window_days is not the longest validation window (252), the double of it is.
    assert repository.calls == [(SYMBOLS, 504, 1000)]


def test_backtest_writes_the_gold_report_from_the_clock(gold: None) -> None:
    deps, _repository = _deps(_matrix(), _FakeValidation(_result()))
    response = run_backtest({}, deps)  # type: ignore[arg-type]
    run_date = "2026-02-09"
    assert response["run_date"] == run_date
    assert response["selected"] == {
        "gamma": 4.0,
        "transaction_cost_lambda": 0.001,
        "alpha": 0.5,
        "window_days": 126,
        "validation_sharpe": 0.8,
    }
    assert response["out_of_sample"] == {
        "optimizer": {
            "annualized_return": 0.08,
            "annualized_volatility": 0.12,
            "sharpe_ratio": 0.8,
            "max_drawdown": -0.15,
        }
    }
    partition_key = BACKTEST_KEY_TEMPLATE.format(run_date=run_date)
    artifact = _read(partition_key)
    assert _read(BACKTEST_LATEST_KEY) == artifact
    assert artifact["metadata"]["code_version"] == "abc123"
    assert artifact["metadata"]["source"] == "alpaca"
    assert artifact["selected"]["validation_sharpe"] == 0.8


def test_backtest_event_overrides_are_respected(gold: None) -> None:
    deps, _repository = _deps(_matrix(), _FakeValidation(_result()))
    event = {"run_id": "run-x", "run_date": "2026-02-09"}
    response = run_backtest(event, deps)  # type: ignore[arg-type]
    assert response["run_id"] == "run-x"
    assert response["run_date"] == "2026-02-09"
    assert _read(BACKTEST_KEY_TEMPLATE.format(run_date="2026-02-09"))["metadata"]["run_id"] == (
        "run-x"
    )


def test_backtest_failure_leaves_no_artifacts(gold: None) -> None:
    deps, _repository = _deps(_matrix(), _FailingValidation())
    with pytest.raises(DataValidationError):
        run_backtest({}, deps)  # type: ignore[arg-type]
    assert _gold_keys() == []


def test_backtest_attributes_the_report_to_the_yfinance_silver(gold: None) -> None:
    deps, _repository = _deps(
        replace(_matrix(), sources=frozenset({"yfinance"})),
        _FakeValidation(_result()),
    )
    run_backtest({}, deps)  # type: ignore[arg-type]
    artifact = _read(BACKTEST_KEY_TEMPLATE.format(run_date="2026-02-09"))
    assert artifact["metadata"]["source"] == "yfinance"


def test_backtest_labels_mixed_silver_data(gold: None) -> None:
    deps, _repository = _deps(
        replace(_matrix(), sources=frozenset({"alpaca", "yfinance"})),
        _FakeValidation(_result()),
    )
    run_backtest({}, deps)  # type: ignore[arg-type]
    artifact = _read(BACKTEST_KEY_TEMPLATE.format(run_date="2026-02-09"))
    assert artifact["metadata"]["source"] == "mixed"


def test_backtest_without_source_metadata_fails_closed(gold: None) -> None:
    deps, _repository = _deps(replace(_matrix(), sources=frozenset()), _FakeValidation(_result()))
    with pytest.raises(ConfigurationError):
        run_backtest({}, deps)  # type: ignore[arg-type]
