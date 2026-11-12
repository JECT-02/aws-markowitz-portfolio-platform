from __future__ import annotations

from pydantic import ValidationError

from common.errors import DataValidationError
from common.models import Bar, BarSeries, RawBarSeries


def parse_series(raw: RawBarSeries) -> BarSeries:
    """Parse a canonical raw payload into a validated bar series."""
    try:
        bars = [Bar.model_validate(item) for item in raw.payload]
        return BarSeries(symbol=raw.symbol, bars=bars)
    except ValidationError as exc:
        raise DataValidationError(f"{raw.symbol}: invalid bar payload") from exc
