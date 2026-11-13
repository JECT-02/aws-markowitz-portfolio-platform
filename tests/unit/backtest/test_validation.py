from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from backtest.core import validation as validation_module
from backtest.core.engine import BacktestResult
from backtest.core.validation import (
    TemporalValidation,
    _period_metrics,
    _slice,
    _split_indices,
)
from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError
from optimizer.core.repository import ReturnsMatrix

SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "TLT", "GLD"]


def _business_dates(count: int) -> list[date]:
    dates: list[date] = []
    current = date(2023, 1, 2)
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _matrix(rows: int = 150) -> ReturnsMatrix:
    generator = np.random.default_rng(21)
    values = generator.normal(0.0004, 0.008, size=(rows, len(SYMBOLS)))
    for start in range(10, rows, 13):
        values[start, :] = -0.011
    return ReturnsMatrix(
        symbols=list(SYMBOLS),
        dates=_business_dates(rows),
        values=values,
        excluded={},
    )


def _strategy() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "risk_aversion": 2.0,
            "transaction_cost_lambda": 0.001,
            "return_shrinkage_alpha": 0.5,
            "risk_free_rate_annual": 0.04,
            "max_forward_fill_days": 2,
            "window_days": 30,
            "ingest_lookback_days": 420,
            "rebalance_l1_threshold": 0.10,
            "validation_gammas": [1.0, 2.0],
            "validation_lambdas": [0.0, 0.001],
            "validation_alphas": [0.0, 0.5],
            "validation_windows": [20, 30],
            "validation_folds": 3,
        }
    )


def _validation() -> TemporalValidation:
    return TemporalValidation(
        UniverseConfig(tickers=SYMBOLS, max_weight=0.20),
        _strategy(),
    )


def test_validation_selects_best_candidate_and_reports_test_metrics() -> None:
    result = _validation().run(_matrix())
    assert len(result.candidates) == 16
    assert result.selected is result.candidates[0]
    assert result.selected.validation_sharpe == max(
        candidate.validation_sharpe for candidate in result.candidates
    )
    assert len(result.selected.fold_metrics) == 3
    assert set(result.out_of_sample_metrics) == {
        "optimizer",
        "equal_weight",
        "minimum_variance",
        "spy",
    }
    assert set(result.in_sample_metrics) == {
        "optimizer",
        "equal_weight",
        "minimum_variance",
        "spy",
    }
    for metrics in result.out_of_sample_metrics.values():
        assert metrics.annualized_volatility > 0.0
        assert np.isfinite(metrics.sharpe_ratio)
    assert set(result.sensitivity) == {20, 30}
    for window, metrics in result.sensitivity.items():
        best = max(
            (candidate for candidate in result.candidates if candidate.window_days == window),
            key=lambda candidate: candidate.validation_sharpe,
        )
        assert metrics == best.fold_metrics[-1]


def test_validation_requires_enough_history() -> None:
    with pytest.raises(DataValidationError):
        _validation().run(_matrix(rows=60))


def test_validation_rejects_empty_windows() -> None:
    strategy = _strategy().model_copy(update={"validation_windows": []})
    validation = TemporalValidation(
        UniverseConfig(tickers=SYMBOLS, max_weight=0.20),
        strategy,
    )
    with pytest.raises(DataValidationError):
        validation.run(_matrix())


def test_validation_rejects_too_many_folds() -> None:
    strategy = _strategy().model_copy(update={"validation_folds": 20})
    validation = TemporalValidation(
        UniverseConfig(tickers=SYMBOLS, max_weight=0.20),
        strategy,
    )
    with pytest.raises(DataValidationError, match="fold count"):
        validation.run(_matrix())


def test_split_rejects_short_validation_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validation_module, "TRAIN_FRACTION", 0.79)
    monkeypatch.setattr(validation_module, "VALIDATION_FRACTION", 0.80)
    with pytest.raises(DataValidationError, match="validation period"):
        _split_indices(_matrix(rows=100), [20], 3)


def test_split_rejects_short_test_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validation_module, "VALIDATION_FRACTION", 0.99)
    with pytest.raises(DataValidationError, match="test period"):
        _split_indices(_matrix(rows=100), [20], 3)


def test_slice_preserves_symbols_and_exclusions() -> None:
    matrix = _matrix(rows=60)
    sliced = _slice(matrix, 10, 40)
    assert sliced.symbols == matrix.symbols
    assert sliced.dates == matrix.dates[10:40]
    assert sliced.values.shape == (30, len(SYMBOLS))
    assert sliced.excluded == {}


def test_period_metrics_requires_enough_observations() -> None:
    matrix = _matrix(rows=20)
    assert (
        _period_metrics(
            matrix.dates,
            matrix.values[:, 0],
            matrix.dates[-1],
            None,
            0.0,
        )
        is None
    )


def test_period_metrics_rejects_zero_volatility() -> None:
    matrix = _matrix(rows=20)
    returns = np.zeros(20)
    assert _period_metrics(matrix.dates, returns, matrix.dates[0], None, 0.0) is None


def test_validation_without_candidates_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_run(*_args: object, **_kwargs: object) -> BacktestResult:
        raise DataValidationError("not enough observations")

    monkeypatch.setattr(validation_module.BacktestEngine, "run", failing_run)
    with pytest.raises(DataValidationError, match="no parameter combination"):
        _validation().run(_matrix())


def test_validation_requires_out_of_sample_optimizer_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run = validation_module.BacktestEngine.run
    matrix = _matrix()
    _, validation_end = _split_indices(matrix, [20, 30], 3)
    cutoff = matrix.dates[validation_end]

    def truncated_run(
        engine: validation_module.BacktestEngine,
        data: object,
        mode: str = "optimizer",
    ) -> BacktestResult:
        result = original_run(engine, data, mode)  # type: ignore[arg-type]
        if mode != "optimizer":
            return result
        keep = [index for index, day in enumerate(result.dates) if day < cutoff]
        return BacktestResult(
            mode=result.mode,
            dates=[result.dates[index] for index in keep],
            returns=result.returns[keep],
            equity=result.equity[keep],
            final_weights=result.final_weights,
            rebalances=result.rebalances,
            metrics=result.metrics,
        )

    monkeypatch.setattr(validation_module.BacktestEngine, "run", truncated_run)
    with pytest.raises(DataValidationError, match="no optimizer observations"):
        _validation().run(matrix)
