from __future__ import annotations

import pandas as pd
import pyarrow as pa
from pydantic import ValidationError

from clean.core.returns import AlignedReturns, build_returns
from common.errors import DataValidationError
from common.logging import get_logger
from common.models import BarSeries, CleanResult, RawBarSeries, RunMetadata
from common.storage import ObjectStorage
from ingest.core.keys import bronze_object_key
from ingest.core.parsing import parse_series
from ingest.core.validation import validate_bar_series

logger = get_logger(__name__)

_SILVER_SCHEMA = pa.schema(
    [
        pa.field("date", pa.date32(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("log_return", pa.float64(), nullable=False),
    ]
)


class CleanService:
    """Builds aligned silver log-return partitions from bronze payloads."""

    def __init__(
        self,
        storage: ObjectStorage,
        bronze_bucket: str,
        silver_bucket: str,
    ) -> None:
        self._storage = storage
        self._bronze_bucket = bronze_bucket
        self._silver_bucket = silver_bucket

    def run(
        self,
        metadata: RunMetadata,
        symbols: list[str],
        max_forward_fill_days: int,
    ) -> CleanResult:
        """Clean bronze payloads and write monthly silver partitions."""
        if max_forward_fill_days < 0:
            raise DataValidationError("max_forward_fill_days must be non-negative")
        series_by_symbol: dict[str, BarSeries] = {}
        missing: list[str] = []
        invalid: dict[str, str] = {}
        for symbol in symbols:
            series = self._load_series(metadata, symbol)
            if series is None:
                missing.append(symbol)
                continue
            try:
                validate_bar_series(series)
            except DataValidationError as exc:
                # Fail-closed: quarantined payloads must never reach silver.
                logger.warning(
                    "bronze payload rejected by clean validation",
                    extra={"symbol": symbol, "detail": str(exc)},
                )
                invalid[symbol] = "invalid_data"
                continue
            series_by_symbol[symbol] = series
        if not series_by_symbol:
            raise DataValidationError("no bronze series available for cleaning")
        aligned = build_returns(series_by_symbol, max_forward_fill_days)
        partitions = self._write_partitions(metadata, aligned)
        return CleanResult(
            metadata=metadata,
            used=sorted(series_by_symbol),
            missing=missing,
            excluded={**invalid, **aligned.excluded},
            partitions=partitions,
        )

    def _load_series(self, metadata: RunMetadata, symbol: str) -> BarSeries | None:
        key = bronze_object_key(metadata.run_date, metadata.source, symbol)
        if not self._storage.exists(self._bronze_bucket, key):
            logger.warning(
                "missing bronze payload",
                extra={"symbol": symbol, "key": key},
            )
            return None
        document = self._storage.read_json(self._bronze_bucket, key)
        payload = document.get("bars")
        if not isinstance(payload, list):
            raise DataValidationError(f"{symbol}: bronze document has no bars list")
        try:
            raw = RawBarSeries.model_validate({"symbol": symbol, "payload": payload})
        except ValidationError as exc:
            raise DataValidationError(f"{symbol}: invalid bronze bars") from exc
        return parse_series(raw)

    def _write_partitions(self, metadata: RunMetadata, aligned: AlignedReturns) -> list[str]:
        grouped: dict[tuple[int, int], list[dict[str, object]]] = {}
        for timestamp in aligned.frame.index:
            moment = pd.Timestamp(str(timestamp))
            bucket = grouped.setdefault((moment.year, moment.month), [])
            row = aligned.frame.loc[timestamp]
            for symbol, value in row.items():
                bucket.append(
                    {
                        "date": moment.date(),
                        "symbol": str(symbol),
                        "log_return": float(value),
                    }
                )
        partitions: list[str] = []
        for (year, month), records in sorted(grouped.items()):
            key = f"year={year:04d}/month={month:02d}/returns.parquet"
            table = pa.Table.from_pylist(records, schema=_SILVER_SCHEMA)
            table = table.replace_schema_metadata(
                {
                    b"run_id": metadata.run_id.encode("utf-8"),
                    b"run_date": metadata.run_date.isoformat().encode("utf-8"),
                    b"code_version": metadata.code_version.encode("utf-8"),
                    b"source": metadata.source.value.encode("utf-8"),
                }
            )
            self._storage.write_parquet(self._silver_bucket, key, table)
            partitions.append(key)
        logger.info(
            "silver partitions written",
            extra={"partitions": partitions, "symbols": len(aligned.frame.columns)},
        )
        return partitions
