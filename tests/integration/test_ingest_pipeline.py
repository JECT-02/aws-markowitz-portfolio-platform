from __future__ import annotations

from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from clean.core.service import CleanService
from common.config import StrategyConfig, UniverseConfig
from common.models import DataSource, RawBarSeries, RunMetadata
from common.storage import S3Storage
from ingest.core.service import IngestService
from ingest.handler import IngestDependencies, run_ingestion


class _FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


class _OpenCalendar:
    def is_trading_day(self, day: date) -> bool:
        return True

    def trading_days(self, start: date, end: date) -> list[object]:
        raise NotImplementedError


class _FakeSource:
    def __init__(self, name: str, outcome: list[RawBarSeries] | Exception) -> None:
        self.name = name
        self._outcome = outcome

    def fetch_daily_bars(self, request: object) -> list[RawBarSeries]:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _raw(symbol: str, closes: dict[int, float]) -> RawBarSeries:
    return RawBarSeries(
        symbol=symbol,
        payload=[
            {
                "symbol": symbol,
                "timestamp": datetime(2024, 1, day, 21, 0, tzinfo=UTC),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
            }
            for day, close in sorted(closes.items())
        ],
    )


@mock_aws
def test_ingest_then_clean_writes_silver() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    for bucket in ("bronze", "quarantine", "silver"):
        client.create_bucket(Bucket=bucket)
    storage = S3Storage(client)
    ingest = IngestService(
        primary=_FakeSource("alpaca", [_raw("SPY", {2: 100.0, 3: 110.0})]),
        fallback=_FakeSource("yfinance", []),
        storage=storage,
        bronze_bucket="bronze",
        quarantine_bucket="quarantine",
    )
    deps = IngestDependencies(
        service=ingest,
        calendar=_OpenCalendar(),
        clock=_FixedClock(datetime(2024, 1, 3, 22, 0, tzinfo=UTC)),
        universe=UniverseConfig(tickers=["SPY"], max_weight=0.20),
        strategy=StrategyConfig(max_forward_fill_days=2),
        code_version="abc",
    )
    outcome = run_ingestion({"run_date": "2024-01-03", "run_id": "run-1"}, deps)
    assert outcome["status"] == "COMPLETED"
    assert outcome["source"] == "alpaca"
    metadata = RunMetadata(
        run_id="run-1",
        run_date=date(2024, 1, 3),
        code_version="abc",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 3, 22, 0, tzinfo=UTC),
    )
    clean = CleanService(storage, "bronze", "silver")
    result = clean.run(metadata, ["SPY"], 2)
    assert result.partitions == ["year=2024/month=01/returns.parquet"]
    table = storage.read_parquet("silver", result.partitions[0])
    assert table.num_rows == 1
    assert table.column("log_return").to_pylist()[0] == pytest.approx(0.09531017980432493)
