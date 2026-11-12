from __future__ import annotations

from datetime import date, datetime

from common.errors import DataValidationError, ExternalSourceError, MarketDataUnavailableError
from common.logging import get_logger
from common.models import (
    BarFetchRequest,
    DataSource,
    IngestResult,
    QuarantineRecord,
    RawBarSeries,
    RunMetadata,
)
from common.storage import ObjectStorage
from ingest.adapters.base import MarketDataSource
from ingest.core.keys import bronze_object_key, quarantine_object_key
from ingest.core.parsing import parse_series
from ingest.core.validation import validate_bar_series

logger = get_logger(__name__)


class IngestService:
    """Fetches, persists and validates daily bars for the configured universe."""

    def __init__(
        self,
        primary: MarketDataSource,
        fallback: MarketDataSource,
        storage: ObjectStorage,
        bronze_bucket: str,
        quarantine_bucket: str,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._storage = storage
        self._bronze_bucket = bronze_bucket
        self._quarantine_bucket = quarantine_bucket

    def run(
        self,
        metadata: RunMetadata,
        symbols: list[str],
        start: date,
        end: date,
    ) -> IngestResult:
        """Run ingestion and return accepted and quarantined bronze keys."""
        request = BarFetchRequest(symbols=symbols, start=start, end=end)
        adapter, raw_series = self._fetch(request)
        source = DataSource(adapter.name)
        run_metadata = metadata.model_copy(update={"source": source})
        by_symbol = {item.symbol: item for item in raw_series}
        accepted: dict[str, str] = {}
        quarantined: dict[str, str] = {}
        for symbol in request.symbols:
            raw = by_symbol.get(symbol)
            if raw is None or not raw.payload:
                quarantined[symbol] = self._quarantine(
                    run_metadata,
                    symbol,
                    "no_data",
                    "source returned no bars",
                    [],
                )
                continue
            bronze_key = bronze_object_key(run_metadata.run_date, source, symbol)
            self._storage.write_json_if_absent(
                self._bronze_bucket,
                bronze_key,
                {
                    "metadata": run_metadata.model_dump(mode="json"),
                    "bars": _json_payload(raw.payload),
                },
            )
            try:
                validate_bar_series(parse_series(raw))
            except DataValidationError as exc:
                quarantined[symbol] = self._quarantine(
                    run_metadata,
                    symbol,
                    "invalid_data",
                    str(exc),
                    _json_payload(raw.payload),
                )
                logger.warning(
                    "symbol quarantined",
                    extra={"symbol": symbol, "reason": "invalid_data", "detail": str(exc)},
                )
                continue
            accepted[symbol] = bronze_key
        return IngestResult(
            metadata=run_metadata,
            accepted=accepted,
            quarantined=quarantined,
        )

    def _fetch(
        self,
        request: BarFetchRequest,
    ) -> tuple[MarketDataSource, list[RawBarSeries]]:
        try:
            return self._primary, self._primary.fetch_daily_bars(request)
        except ExternalSourceError as primary_error:
            logger.warning(
                "primary source failed; using fallback",
                extra={"source": self._primary.name, "detail": str(primary_error)},
            )
        try:
            return self._fallback, self._fallback.fetch_daily_bars(request)
        except ExternalSourceError as fallback_error:
            raise MarketDataUnavailableError("all market data sources failed") from fallback_error

    def _quarantine(
        self,
        metadata: RunMetadata,
        symbol: str,
        reason_code: str,
        detail: str,
        payload: list[dict[str, object]],
    ) -> str:
        record = QuarantineRecord(
            metadata=metadata,
            symbol=symbol,
            reason_code=reason_code,
            detail=detail,
            payload=payload,
        )
        key = quarantine_object_key(metadata.run_date, metadata.source, symbol, reason_code)
        self._storage.write_json(
            self._quarantine_bucket,
            key,
            record.model_dump(mode="json"),
        )
        return key


def _json_payload(payload: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in payload:
        rows.append(
            {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in item.items()
            }
        )
    return rows
