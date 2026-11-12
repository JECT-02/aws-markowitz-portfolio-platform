from datetime import UTC, date, datetime
from typing import Any

import pytest

from common.errors import ExternalSourceError, MarketDataUnavailableError
from common.models import DataSource, RawBarSeries, RunMetadata
from ingest.core.keys import bronze_object_key
from ingest.core.service import IngestService


class _MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def exists(self, bucket: str, key: str) -> bool:
        return key in self.objects.get(bucket, {})

    def write_json_if_absent(self, bucket: str, key: str, payload: dict[str, Any]) -> bool:
        if self.exists(bucket, key):
            return False
        self.write_json(bucket, key, payload)
        return True

    def write_json(self, bucket: str, key: str, payload: dict[str, Any]) -> None:
        self.objects.setdefault(bucket, {})[key] = payload

    def read_json(self, bucket: str, key: str) -> dict[str, Any]:
        return self.objects[bucket][key]

    def write_parquet(self, bucket: str, key: str, table: object) -> None:
        raise NotImplementedError

    def read_parquet(self, bucket: str, key: str) -> object:
        raise NotImplementedError

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        return sorted(key for key in self.objects.get(bucket, {}) if key.startswith(prefix))


class _FakeSource:
    def __init__(self, name: str, outcome: list[RawBarSeries] | Exception) -> None:
        self.name = name
        self._outcome = outcome

    def fetch_daily_bars(self, request: object) -> list[RawBarSeries]:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-1",
        run_date=date(2024, 1, 2),
        code_version="abc",
        source=DataSource.ALPACA,
        fetched_at=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
    )


def _raw(symbol: str = "SPY", close: float = 100.0) -> RawBarSeries:
    return RawBarSeries(
        symbol=symbol,
        payload=[
            {
                "symbol": symbol,
                "timestamp": datetime(2024, 1, 2, tzinfo=UTC),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
            }
        ],
    )


def _service(
    primary: _FakeSource,
    fallback: _FakeSource | None = None,
) -> tuple[IngestService, _MemoryStorage]:
    storage = _MemoryStorage()
    service = IngestService(
        primary=primary,
        fallback=fallback or _FakeSource("yfinance", []),
        storage=storage,
        bronze_bucket="bronze",
        quarantine_bucket="quarantine",
    )
    return service, storage


def test_valid_symbol_is_accepted_and_bronze_is_written() -> None:
    service, storage = _service(_FakeSource("alpaca", [_raw()]))
    result = service.run(_metadata(), ["SPY"], date(2024, 1, 2), date(2024, 1, 2))
    key = bronze_object_key(date(2024, 1, 2), DataSource.ALPACA, "SPY")
    assert result.accepted == {"SPY": key}
    assert result.quarantined == {}
    assert result.metadata.source is DataSource.ALPACA
    assert storage.objects["bronze"][key]["bars"][0]["timestamp"] == "2024-01-02T00:00:00+00:00"


def test_missing_symbol_is_quarantined() -> None:
    service, storage = _service(_FakeSource("alpaca", [_raw("SPY")]))
    result = service.run(_metadata(), ["SPY", "QQQ"], date(2024, 1, 2), date(2024, 1, 2))
    assert set(result.accepted) == {"SPY"}
    assert set(result.quarantined) == {"QQQ"}
    record = storage.objects["quarantine"][result.quarantined["QQQ"]]
    assert record["reason_code"] == "no_data"


def test_invalid_series_keeps_bronze_and_quarantines() -> None:
    service, storage = _service(_FakeSource("alpaca", [_raw(close=0.0)]))
    result = service.run(_metadata(), ["SPY"], date(2024, 1, 2), date(2024, 1, 2))
    key = bronze_object_key(date(2024, 1, 2), DataSource.ALPACA, "SPY")
    assert result.accepted == {}
    assert key in storage.objects["bronze"]
    record = storage.objects["quarantine"][result.quarantined["SPY"]]
    assert record["reason_code"] == "invalid_data"


def test_invalid_payload_is_quarantined() -> None:
    raw = RawBarSeries(symbol="SPY", payload=[{"symbol": "SPY"}])
    service, storage = _service(_FakeSource("alpaca", [raw]))
    result = service.run(_metadata(), ["SPY"], date(2024, 1, 2), date(2024, 1, 2))
    record = storage.objects["quarantine"][result.quarantined["SPY"]]
    assert record["reason_code"] == "invalid_data"


def test_primary_failure_uses_fallback_source() -> None:
    primary = _FakeSource("alpaca", ExternalSourceError("down"))
    fallback = _FakeSource("yfinance", [_raw()])
    service, _ = _service(primary, fallback)
    result = service.run(_metadata(), ["SPY"], date(2024, 1, 2), date(2024, 1, 2))
    assert result.metadata.source is DataSource.YFINANCE
    assert result.accepted["SPY"].startswith("year=2024/month=01/day=02/source=yfinance/")


def test_all_sources_failing_raises() -> None:
    primary = _FakeSource("alpaca", ExternalSourceError("down"))
    fallback = _FakeSource("yfinance", ExternalSourceError("also down"))
    service, _ = _service(primary, fallback)
    with pytest.raises(MarketDataUnavailableError):
        service.run(_metadata(), ["SPY"], date(2024, 1, 2), date(2024, 1, 2))


def test_bronze_is_never_overwritten() -> None:
    service, storage = _service(_FakeSource("alpaca", [_raw()]))
    key = bronze_object_key(date(2024, 1, 2), DataSource.ALPACA, "SPY")
    original = {"metadata": {}, "bars": []}
    storage.write_json("bronze", key, original)
    result = service.run(_metadata(), ["SPY"], date(2024, 1, 2), date(2024, 1, 2))
    assert result.accepted == {"SPY": key}
    assert storage.objects["bronze"][key] == original
