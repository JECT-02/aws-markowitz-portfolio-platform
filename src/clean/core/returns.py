from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from common.errors import DataValidationError
from common.models import BarSeries

_EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class AlignedReturns:
    """Wide log-return frame plus symbols excluded for insufficient history."""

    frame: pd.DataFrame
    excluded: dict[str, str]


def build_returns(
    series_by_symbol: Mapping[str, BarSeries],
    max_forward_fill_days: int,
) -> AlignedReturns:
    """Align close prices and compute daily log returns per symbol."""
    if not series_by_symbol:
        raise DataValidationError("no series supplied for cleaning")
    prices: dict[str, pd.Series] = {}
    for symbol, series in series_by_symbol.items():
        prices[symbol] = _close_prices(series)
    aligned = pd.DataFrame(prices).sort_index()
    if max_forward_fill_days > 0:
        aligned = aligned.ffill(limit=max_forward_fill_days)
    excluded = {
        symbol: "insufficient_history" for symbol in aligned.columns if aligned[symbol].isna().any()
    }
    if excluded:
        aligned = aligned.drop(columns=list(excluded))
    if aligned.empty or len(aligned) < 2:
        raise DataValidationError("insufficient aligned history to compute returns")
    returns = (aligned / aligned.shift(1)).map(np.log).dropna(how="any")
    if returns.empty:
        raise DataValidationError("no log returns produced")
    return AlignedReturns(frame=returns, excluded=excluded)


def _close_prices(series: BarSeries) -> pd.Series:
    if not series.bars:
        raise DataValidationError(f"{series.symbol}: empty series")
    index = pd.DatetimeIndex([bar.timestamp for bar in series.bars], tz="UTC")
    index = index.tz_convert(_EASTERN).normalize().tz_localize(None)
    if index.has_duplicates:
        raise DataValidationError(f"{series.symbol}: duplicate trading dates")
    closes = [bar.close for bar in series.bars]
    if any(close <= 0.0 for close in closes):
        raise DataValidationError(f"{series.symbol}: non-positive close price")
    prices = pd.Series(closes, index=index, dtype="float64")
    return prices.sort_index()
