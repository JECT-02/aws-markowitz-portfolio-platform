from __future__ import annotations

import math
from datetime import date
from typing import Protocol

from common.errors import NotFoundError, StorageError
from common.storage import ObjectStorage


class ProposalPrices(Protocol):
    """Source of the reference prices stored at proposal time."""

    def load(self, run_date: date) -> dict[str, float]: ...


class GoldPricesRepository:
    """Reads the per-symbol reference prices written by the optimizer."""

    def __init__(self, storage: ObjectStorage, gold_bucket: str) -> None:
        self._storage = storage
        self._gold_bucket = gold_bucket

    def load(self, run_date: date) -> dict[str, float]:
        """Load the reference prices for a run date.

        Raises ``NotFoundError`` when the run date has no stored prices: the
        drift check must fail closed instead of silently passing.
        """
        key = f"run_date={run_date.isoformat()}/prices.json"
        if not self._storage.exists(self._gold_bucket, key):
            raise NotFoundError(f"reference prices not found for {key}")
        document = self._storage.read_json(self._gold_bucket, key)
        raw = document.get("prices")
        if not isinstance(raw, dict) or not raw:
            raise StorageError(f"reference prices at {key} are empty or malformed")
        prices: dict[str, float] = {}
        for symbol, value in raw.items():
            price = float(value)
            if not math.isfinite(price) or price <= 0.0:
                raise StorageError(f"reference price for {symbol} must be finite and positive")
            prices[str(symbol).upper()] = price
        return prices
