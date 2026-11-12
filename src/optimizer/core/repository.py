from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np
import pandas as pd

from common.errors import DataValidationError
from common.storage import ObjectStorage

REQUIRED_COLUMNS = frozenset({"date", "symbol", "log_return"})


@dataclass(frozen=True)
class ReturnsMatrix:
    """Aligned log-return matrix ready for estimation."""

    symbols: list[str]
    dates: list[date]
    values: np.ndarray
    excluded: dict[str, str]

    @property
    def n_assets(self) -> int:
        return len(self.symbols)

    @property
    def n_observations(self) -> int:
        return len(self.dates)


class ReturnsRepository(Protocol):
    """Source of aligned log-return matrices."""

    def load(self, symbols: list[str], window_days: int) -> ReturnsMatrix: ...


class SilverReturnsRepository:
    """Reads aligned log returns from the silver lakehouse."""

    def __init__(self, storage: ObjectStorage, silver_bucket: str) -> None:
        self._storage = storage
        self._silver_bucket = silver_bucket

    def load(self, symbols: list[str], window_days: int) -> ReturnsMatrix:
        """Load the trailing window of silver returns for the requested symbols."""
        if not symbols:
            raise DataValidationError("at least one symbol is required")
        if window_days < 2:
            raise DataValidationError("window_days must be at least two")
        keys = self._storage.list_keys(self._silver_bucket, "year=")
        if not keys:
            raise DataValidationError("no silver partitions available")
        frames = [self._read_partition(key) for key in keys]
        combined = pd.concat(frames, ignore_index=True)
        duplicates = combined.duplicated(subset=["date", "symbol"])
        if bool(duplicates.any()):
            raise DataValidationError("silver partitions contain duplicate observations")
        pivot = combined.pivot(index="date", columns="symbol", values="log_return")
        pivot = pivot.sort_index()
        requested = [symbol.strip().upper() for symbol in symbols]
        available = [symbol for symbol in requested if symbol in pivot.columns]
        excluded = {symbol: "no_data" for symbol in requested if symbol not in pivot.columns}
        for symbol in available:
            column = pivot[symbol]
            if int(column.notna().sum()) < window_days:
                excluded[symbol] = "insufficient_history"
            elif bool(column.isna().any()):
                excluded[symbol] = "missing_values"
        valid = [symbol for symbol in available if symbol not in excluded]
        if not valid:
            raise DataValidationError("no symbol has sufficient silver history")
        frame = pivot[valid].tail(window_days)
        values = frame.to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise DataValidationError("silver returns contain non-finite values")
        dates = [pd.Timestamp(index).date() for index in frame.index]
        return ReturnsMatrix(
            symbols=valid,
            dates=dates,
            values=values,
            excluded=excluded,
        )

    def _read_partition(self, key: str) -> pd.DataFrame:
        table = self._storage.read_parquet(self._silver_bucket, key)
        frame: pd.DataFrame = table.to_pandas()
        missing = REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise DataValidationError(
                f"silver partition {key} is missing columns: {', '.join(sorted(missing))}"
            )
        return frame.loc[:, ["date", "symbol", "log_return"]]
