from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import numpy as np
import pandas as pd

from common.errors import DataValidationError
from common.models import DataSource
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

    def load(
        self,
        symbols: list[str],
        window_days: int,
        max_days: int | None = None,
    ) -> ReturnsMatrix: ...


class PricesRepository(Protocol):
    """Source of the reference prices captured at proposal time."""

    def load_reference_prices(self, run_date: date, source: DataSource) -> dict[str, float]: ...


class BronzePricesRepository:
    """Reads the last adjusted close of every bronze bar series."""

    def __init__(self, storage: ObjectStorage, bronze_bucket: str) -> None:
        self._storage = storage
        self._bronze_bucket = bronze_bucket

    def load_reference_prices(self, run_date: date, source: DataSource) -> dict[str, float]:
        """Load the last close per symbol from the bronze run partition."""
        prefix = (
            f"year={run_date.year:04d}/month={run_date.month:02d}/day={run_date.day:02d}"
            f"/source={source.value}/"
        )
        keys = self._storage.list_keys(self._bronze_bucket, prefix)
        prices: dict[str, float] = {}
        for key in keys:
            symbol = _symbol_from_key(key, prefix)
            if symbol is None:
                continue
            document = self._storage.read_json(self._bronze_bucket, key)
            close = _last_close(document.get("bars"))
            if close is not None:
                prices[symbol] = close
        if not prices:
            raise DataValidationError("no bronze reference prices for the run date")
        return prices


def _symbol_from_key(key: str, prefix: str) -> str | None:
    remainder = key[len(prefix) :]
    if not remainder.startswith("symbol=") or not remainder.endswith("/bars.json"):
        return None
    symbol = remainder[len("symbol=") : -len("/bars.json")]
    return symbol.strip().upper() or None


def _last_close(bars: object) -> float | None:
    if not isinstance(bars, list) or not bars:
        return None
    closes = [float(bar["close"]) for bar in bars if isinstance(bar, dict) and "close" in bar]
    valid = [close for close in closes if close > 0.0 and np.isfinite(close)]
    return valid[-1] if valid else None


class SilverReturnsRepository:
    """Reads aligned log returns from the silver lakehouse."""

    def __init__(self, storage: ObjectStorage, silver_bucket: str) -> None:
        self._storage = storage
        self._silver_bucket = silver_bucket

    def load(
        self,
        symbols: list[str],
        window_days: int,
        max_days: int | None = None,
    ) -> ReturnsMatrix:
        """Load aligned silver returns with at least window_days of history."""
        if not symbols:
            raise DataValidationError("at least one symbol is required")
        if window_days < 2:
            raise DataValidationError("window_days must be at least two")
        if max_days is not None and max_days < window_days:
            raise DataValidationError("max_days must be at least window_days")
        needed = window_days if max_days is None else max_days
        keys = self._storage.list_keys(self._silver_bucket, "year=")
        if not keys:
            raise DataValidationError("no silver partitions available")
        frames = self._read_newest_partitions(keys, needed)
        combined = pd.concat(frames, ignore_index=True)
        duplicates = combined.duplicated(subset=["date", "symbol"])
        if bool(duplicates.any()):
            raise DataValidationError("silver partitions contain duplicate observations")
        pivot = combined.pivot(index="date", columns="symbol", values="log_return")
        pivot = pivot.sort_index()
        window = pivot.tail(needed)
        requested = [symbol.strip().upper() for symbol in symbols]
        excluded = {symbol: "no_data" for symbol in requested if symbol not in window.columns}
        for symbol in requested:
            if symbol in excluded:
                continue
            if int(pivot[symbol].notna().sum()) < window_days:
                excluded[symbol] = "insufficient_history"
            elif bool(window[symbol].isna().any()):
                excluded[symbol] = "missing_values"
        valid = [
            symbol for symbol in requested if symbol in window.columns and symbol not in excluded
        ]
        if not valid:
            raise DataValidationError("no symbol has sufficient silver history")
        frame = window[valid]
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

    def _read_newest_partitions(self, keys: list[str], needed: int) -> list[pd.DataFrame]:
        frames: list[pd.DataFrame] = []
        seen_dates: set[object] = set()
        for key in reversed(keys):
            frame = self._read_partition(key)
            frames.append(frame)
            seen_dates.update(frame["date"].tolist())
            if len(seen_dates) >= needed:
                break
        return frames

    def _read_partition(self, key: str) -> pd.DataFrame:
        table = self._storage.read_parquet(self._silver_bucket, key)
        frame: pd.DataFrame = table.to_pandas()
        missing = REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            raise DataValidationError(
                f"silver partition {key} is missing columns: {', '.join(sorted(missing))}"
            )
        return frame.loc[:, ["date", "symbol", "log_return"]]
