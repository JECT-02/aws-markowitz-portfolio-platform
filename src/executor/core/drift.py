from __future__ import annotations

import math
from collections.abc import Mapping

from common.errors import BrokerError, PriceDriftExceeded


def check_price_drift(
    proposal_prices: Mapping[str, float],
    current_prices: Mapping[str, float],
    max_drift_pct: float,
) -> None:
    """Reject execution when current prices deviate beyond the threshold.

    Preconditions: both maps hold finite positive prices for overlapping
    symbols; ``max_drift_pct`` is in (0, 1].
    Postconditions: returns silently when the worst relative drift is within
    the threshold; raises ``PriceDriftExceeded`` naming the worst symbol.
    """
    if not proposal_prices:
        raise BrokerError("proposal prices are required for the drift check")
    if not current_prices:
        raise BrokerError("current prices are required for the drift check")
    if not 0.0 < max_drift_pct <= 1.0:
        raise ValueError("max_drift_pct must be in (0, 1]")
    missing = [
        symbol
        for symbol in proposal_prices
        if symbol not in current_prices or current_prices[symbol] <= 0.0
    ]
    if missing:
        raise BrokerError(f"missing current prices for symbols: {', '.join(missing)}")
    worst_symbol = ""
    worst_drift = 0.0
    for symbol, reference in proposal_prices.items():
        if reference <= 0.0 or not math.isfinite(reference):
            raise BrokerError(f"proposal price for {symbol} must be finite and positive")
        drift = abs(current_prices[symbol] - reference) / reference
        if drift > worst_drift:
            worst_symbol = symbol
            worst_drift = drift
    if worst_drift > max_drift_pct:
        raise PriceDriftExceeded(
            f"price drift {worst_drift:.4f} for {worst_symbol} exceeds {max_drift_pct:.4f}"
        )
