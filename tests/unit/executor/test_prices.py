from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from common.errors import NotFoundError, StorageError
from executor.core.prices import GoldPricesRepository

GOLD_BUCKET = "gold"


class _StubStorage:
    def __init__(self, documents: dict[str, Any]) -> None:
        self._documents = documents

    def exists(self, bucket: str, key: str) -> bool:
        return bucket in self._documents and key in self._documents[bucket]

    def read_json(self, bucket: str, key: str) -> Any:
        try:
            return self._documents[bucket][key]
        except KeyError as exc:
            raise NotFoundError(f"missing object {key}") from exc


class TestGoldPricesRepository:
    def test_loads_and_normalizes_prices(self) -> None:
        storage = _StubStorage(
            {
                GOLD_BUCKET: {
                    "run_date=2026-11-13/prices.json": {"prices": {"spy": "100.5", "qqq": 50.0}}
                }
            }
        )
        repository = GoldPricesRepository(storage, GOLD_BUCKET)  # type: ignore[arg-type]
        prices = repository.load(date(2026, 11, 13))
        assert prices == {"SPY": 100.5, "QQQ": 50.0}

    def test_missing_object_is_not_found(self) -> None:
        storage = _StubStorage({})
        repository = GoldPricesRepository(storage, GOLD_BUCKET)  # type: ignore[arg-type]
        with pytest.raises(NotFoundError):
            repository.load(date(2026, 11, 13))

    def test_malformed_document_fails_closed(self) -> None:
        storage = _StubStorage({GOLD_BUCKET: {"run_date=2026-11-13/prices.json": {"prices": {}}}})
        repository = GoldPricesRepository(storage, GOLD_BUCKET)  # type: ignore[arg-type]
        with pytest.raises(StorageError, match="malformed"):
            repository.load(date(2026, 11, 13))

    def test_non_numeric_price_fails_closed(self) -> None:
        storage = _StubStorage(
            {GOLD_BUCKET: {"run_date=2026-11-13/prices.json": {"prices": {"SPY": None}}}}
        )
        repository = GoldPricesRepository(storage, GOLD_BUCKET)  # type: ignore[arg-type]
        with pytest.raises(StorageError, match="must be a number"):
            repository.load(date(2026, 11, 13))

    @pytest.mark.parametrize("value", ["0", -5.0, float("nan")])
    def test_invalid_prices_fail_closed(self, value: Any) -> None:
        storage = _StubStorage(
            {GOLD_BUCKET: {"run_date=2026-11-13/prices.json": {"prices": {"SPY": value}}}}
        )
        repository = GoldPricesRepository(storage, GOLD_BUCKET)  # type: ignore[arg-type]
        with pytest.raises(StorageError, match="finite and positive"):
            repository.load(date(2026, 11, 13))
