from datetime import UTC, datetime

import pytest

from clean.core.returns import AlignedReturns, build_returns
from common.errors import DataValidationError
from common.models import Bar, BarSeries


def _series(symbol: str, closes: dict[int, float]) -> BarSeries:
    bars = [
        Bar(
            symbol=symbol,
            timestamp=datetime(2024, 1, day, 21, 0, tzinfo=UTC),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1000.0,
        )
        for day, close in sorted(closes.items())
    ]
    return BarSeries(symbol=symbol, bars=bars)


def _returns(result: AlignedReturns, symbol: str) -> list[float]:
    return [float(value) for value in result.frame[symbol].tolist()]


def test_log_returns_match_manual_values() -> None:
    result = build_returns({"SPY": _series("SPY", {2: 100.0, 3: 110.0, 4: 99.0})}, 0)
    values = _returns(result, "SPY")
    assert values[0] == pytest.approx(0.09531017980432493)
    assert values[1] == pytest.approx(-0.10536051565782628)


def test_forward_fill_is_bounded() -> None:
    spy = _series("SPY", {2: 100.0, 4: 110.0})
    qqq = _series("QQQ", {2: 100.0, 3: 100.0, 4: 100.0})
    filled = build_returns({"SPY": spy, "QQQ": qqq}, 1)
    assert filled.excluded == {}
    assert filled.frame.index.day.tolist() == [3, 4]
    assert _returns(filled, "SPY")[0] == pytest.approx(0.0)
    assert _returns(filled, "SPY")[1] == pytest.approx(0.09531017980432493)
    unfilled = build_returns({"SPY": spy, "QQQ": qqq}, 0)
    assert unfilled.excluded == {"SPY": "insufficient_history"}
    assert list(unfilled.frame.columns) == ["QQQ"]


def test_leading_gap_excludes_symbol() -> None:
    spy = _series("SPY", {3: 100.0, 4: 101.0})
    qqq = _series("QQQ", {2: 100.0, 3: 100.0, 4: 100.0})
    result = build_returns({"SPY": spy, "QQQ": qqq}, 2)
    assert result.excluded == {"SPY": "insufficient_history"}
    assert list(result.frame.columns) == ["QQQ"]


def test_non_positive_close_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        build_returns({"SPY": _series("SPY", {2: 100.0, 3: 0.0})}, 0)


def test_duplicate_trading_dates_are_rejected() -> None:
    first = Bar(
        symbol="SPY",
        timestamp=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1.0,
    )
    second = Bar(
        symbol="SPY",
        timestamp=datetime(2024, 1, 2, 22, 0, tzinfo=UTC),
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1.0,
    )
    with pytest.raises(DataValidationError):
        build_returns({"SPY": BarSeries(symbol="SPY", bars=[first, second])}, 0)


def test_empty_input_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        build_returns({}, 0)
