from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from clean.core.returns import build_returns
from common.models import Bar, BarSeries

_PRICES = st.lists(
    st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    min_size=2,
    max_size=20,
)


def _series(closes: list[float]) -> BarSeries:
    base = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
    bars = [
        Bar(
            symbol="SPY",
            timestamp=base + timedelta(days=index),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
        )
        for index, close in enumerate(closes)
    ]
    return BarSeries(symbol="SPY", bars=bars)


@given(_PRICES)
@settings(max_examples=50)
def test_log_returns_match_manual_computation(closes: list[float]) -> None:
    result = build_returns({"SPY": _series(closes)}, 0)
    expected = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    assert result.frame["SPY"].tolist() == pytest.approx(expected)


@given(_PRICES)
@settings(max_examples=50)
def test_returns_are_finite(closes: list[float]) -> None:
    result = build_returns({"SPY": _series(closes)}, 0)
    values = result.frame.to_numpy()
    assert bool(np.isfinite(values).all())
    assert bool(result.frame.notna().all().all())


@given(_PRICES, st.data())
@settings(max_examples=50)
def test_future_prices_do_not_change_past_returns(
    closes: list[float],
    data: st.DataObject,
) -> None:
    cutoff = data.draw(st.integers(min_value=2, max_value=len(closes)))
    future = data.draw(
        st.lists(
            st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
            min_size=len(closes) - cutoff,
            max_size=len(closes) - cutoff,
        )
    )
    base = build_returns({"SPY": _series(closes)}, 0)
    modified = build_returns({"SPY": _series(closes[:cutoff] + future)}, 0)
    pd.testing.assert_frame_equal(base.frame.iloc[: cutoff - 1], modified.frame.iloc[: cutoff - 1])
