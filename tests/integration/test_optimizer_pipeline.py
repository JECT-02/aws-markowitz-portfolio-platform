from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import boto3
import numpy as np
import pyarrow as pa
import pytest
from moto import mock_aws

from common.config import StrategyConfig, UniverseConfig
from common.models import DataSource, RunMetadata
from common.storage import S3Storage
from optimizer.core.repository import SilverReturnsRepository
from optimizer.core.service import OptimizerService

SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "TLT", "GLD"]
SCHEMA = pa.schema(
    [
        pa.field("date", pa.date32(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("log_return", pa.float64(), nullable=False),
    ]
)


class _FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


def _business_dates(count: int) -> list[date]:
    dates: list[date] = []
    current = date(2024, 1, 1)
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _history(rows: int = 90) -> tuple[list[date], np.ndarray]:
    generator = np.random.default_rng(31)
    values = generator.normal(0.0005, 0.008, size=(rows, len(SYMBOLS)))
    for start in range(9, rows, 11):
        values[start, :] = -0.009
    return _business_dates(rows), values


def _write_partitions(
    storage: S3Storage,
    dates: list[date],
    values: np.ndarray,
) -> list[str]:
    grouped: dict[tuple[int, int], list[dict[str, object]]] = {}
    symbols = SYMBOLS[: values.shape[1]]
    for row, day in enumerate(dates):
        key = (day.year, day.month)
        bucket = grouped.setdefault(key, [])
        for column, symbol in enumerate(symbols):
            bucket.append(
                {
                    "date": day,
                    "symbol": symbol,
                    "log_return": float(values[row, column]),
                }
            )
    keys: list[str] = []
    for (year, month), records in sorted(grouped.items()):
        key = f"year={year:04d}/month={month:02d}/returns.parquet"
        storage.write_parquet(
            "silver",
            key,
            pa.Table.from_pylist(records, schema=SCHEMA),
        )
        keys.append(key)
    return keys


def _strategy() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "risk_aversion": 2.0,
            "transaction_cost_lambda": 0.001,
            "return_shrinkage_alpha": 0.5,
            "risk_free_rate_annual": 0.04,
            "max_forward_fill_days": 2,
            "window_days": 60,
            "ingest_lookback_days": 420,
            "rebalance_l1_threshold": 0.10,
            "validation_gammas": [2.0],
            "validation_lambdas": [0.001],
            "validation_alphas": [0.5],
            "validation_windows": [60],
            "validation_folds": 3,
        }
    )


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-42",
        run_date=date(2024, 5, 6),
        code_version="abc1234",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 5, 6, 21, 0, tzinfo=UTC),
    )


@mock_aws
def test_optimizer_reads_silver_and_writes_gold() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    for bucket in ("silver", "gold"):
        client.create_bucket(Bucket=bucket)
    storage = S3Storage(client)
    dates, values = _history()
    _write_partitions(storage, dates, values)

    service = OptimizerService(
        repository=SilverReturnsRepository(storage, "silver"),
        storage=storage,
        gold_bucket="gold",
        universe=UniverseConfig(tickers=SYMBOLS, max_weight=0.20),
        strategy=_strategy(),
        clock=_FixedClock(datetime(2024, 5, 6, 21, 30, tzinfo=UTC)),
    )
    proposal = service.run(_metadata())

    assert proposal.proposal_id == "2024-05-06-run-42"
    assert proposal.excluded == {}
    assert sum(proposal.weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert min(proposal.weights.values()) >= 0.0
    assert max(proposal.weights.values()) <= 0.20 + 1e-8
    assert set(proposal.metrics) == {
        "optimizer",
        "equal_weight",
        "minimum_variance",
        "spy",
    }
    assert sum(proposal.actual_weights.values()) == pytest.approx(1.0, abs=1e-6)

    proposal_key = "run_date=2024-05-06/proposal.json"
    assert storage.exists("gold", proposal_key)
    assert storage.exists("gold", "latest.json")
    latest = storage.read_json("gold", "latest.json")
    stored = storage.read_json("gold", proposal_key)
    assert latest == stored
    assert latest["proposal_id"] == proposal.proposal_id
    assert latest["metadata"]["code_version"] == "abc1234"
    assert latest["run_date"] == "2024-05-06"


@mock_aws
def test_optimizer_excludes_symbols_without_history() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    for bucket in ("silver", "gold"):
        client.create_bucket(Bucket=bucket)
    storage = S3Storage(client)
    dates, values = _history()
    _write_partitions(storage, dates, values[:, :5])

    symbols = SYMBOLS[:5] + ["VNQ"]
    service = OptimizerService(
        repository=SilverReturnsRepository(storage, "silver"),
        storage=storage,
        gold_bucket="gold",
        universe=UniverseConfig(tickers=symbols, max_weight=0.20),
        strategy=_strategy(),
        clock=_FixedClock(datetime(2024, 5, 6, 21, 30, tzinfo=UTC)),
    )
    proposal = service.run(_metadata())

    assert proposal.excluded == {"VNQ": "no_data"}
    assert set(proposal.weights) == set(SYMBOLS[:5])
