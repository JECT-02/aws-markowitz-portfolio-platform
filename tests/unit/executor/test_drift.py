from __future__ import annotations

import pytest

from common.errors import BrokerError, PriceDriftExceeded
from executor.core.drift import check_price_drift


def test_drift_within_threshold_passes() -> None:
    check_price_drift(
        {"AAA": 100.0, "BBB": 50.0},
        {"AAA": 100.9, "BBB": 50.0},
        max_drift_pct=0.01,
    )


def test_drift_beyond_threshold_raises_naming_worst_symbol() -> None:
    with pytest.raises(PriceDriftExceeded) as error:
        check_price_drift(
            {"AAA": 100.0, "BBB": 50.0},
            {"AAA": 101.5, "BBB": 50.0},
            max_drift_pct=0.01,
        )
    assert "BBB" not in str(error.value)
    assert "AAA" in str(error.value)


def test_drift_is_relative_not_absolute() -> None:
    with pytest.raises(PriceDriftExceeded):
        check_price_drift({"AAA": 1.0}, {"AAA": 1.02}, max_drift_pct=0.01)


def test_missing_current_price_raises_broker_error() -> None:
    with pytest.raises(BrokerError, match="missing current prices"):
        check_price_drift({"AAA": 100.0}, {"BBB": 50.0}, max_drift_pct=0.01)


def test_empty_proposal_prices_raise_broker_error() -> None:
    with pytest.raises(BrokerError, match="proposal prices"):
        check_price_drift({}, {"AAA": 100.0}, max_drift_pct=0.01)


def test_invalid_threshold_is_rejected() -> None:
    with pytest.raises(ValueError):
        check_price_drift({"AAA": 100.0}, {"AAA": 100.0}, max_drift_pct=0.0)
