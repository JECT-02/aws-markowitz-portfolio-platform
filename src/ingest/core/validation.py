from __future__ import annotations

import math

from common.errors import DataValidationError
from common.models import BarSeries

MAX_ABS_DAILY_LOG_RETURN = 0.40


def validate_bar_series(
    series: BarSeries,
    max_abs_log_return: float = MAX_ABS_DAILY_LOG_RETURN,
) -> None:
    """Validate OHLC consistency, positivity and daily return bounds."""
    if not series.bars:
        raise DataValidationError(f"{series.symbol}: empty series")
    previous_close: float | None = None
    for bar in series.bars:
        if bar.open <= 0.0 or bar.high <= 0.0 or bar.low <= 0.0 or bar.close <= 0.0:
            raise DataValidationError(f"{series.symbol}: non-positive price")
        if bar.volume < 0.0:
            raise DataValidationError(f"{series.symbol}: negative volume")
        if bar.high < bar.low:
            raise DataValidationError(f"{series.symbol}: high below low")
        if bar.high < bar.open or bar.high < bar.close:
            raise DataValidationError(f"{series.symbol}: high below open or close")
        if bar.low > bar.open or bar.low > bar.close:
            raise DataValidationError(f"{series.symbol}: low above open or close")
        if previous_close is not None:
            log_return = math.log(bar.close / previous_close)
            if abs(log_return) > max_abs_log_return:
                raise DataValidationError(f"{series.symbol}: anomalous log return {log_return:.6f}")
        previous_close = bar.close
