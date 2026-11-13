from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from backtest.core.engine import BacktestEngine
from common.config import StrategyConfig, UniverseConfig
from common.errors import DataValidationError
from optimizer.core.repository import ReturnsMatrix

SYMBOLS = ["SPY", "QQQ", "IWM", "EFA", "TLT", "GLD"]


def _business_dates(count: int) -> list[date]:
    dates: list[date] = []
    current = date(2024, 1, 1)
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _matrix(rows: int = 130, window: int = 30) -> ReturnsMatrix:
    generator = np.random.default_rng(13)
    values = generator.normal(0.0004, 0.008, size=(rows, len(SYMBOLS)))
    for start in range(10, rows, 17):
        values[start, :] = -0.012
    return ReturnsMatrix(
        symbols=list(SYMBOLS),
        dates=_business_dates(rows),
        values=values,
        excluded={},
    )


def _strategy(**overrides: object) -> StrategyConfig:
    values: dict[str, object] = {
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
        "validation_windows": [30],
        "validation_folds": 3,
    }
    values.update(overrides)
    return StrategyConfig.model_validate(values)


def _engine(strategy: StrategyConfig | None = None) -> BacktestEngine:
    return BacktestEngine(
        strategy if strategy is not None else _strategy(),
        UniverseConfig(tickers=SYMBOLS, max_weight=0.20),
    )


@pytest.mark.parametrize("mode", ["optimizer", "equal_weight", "minimum_variance", "spy"])
def test_all_modes_produce_finite_results(mode: str) -> None:
    result = _engine().run(_matrix(), mode)
    assert result.mode == mode
    assert len(result.returns) == len(result.dates)
    assert bool(np.isfinite(result.returns).all())
    assert bool(np.isfinite(result.equity).all())
    assert result.equity[-1] > 0.0
    assert result.final_weights.sum() == pytest.approx(1.0, abs=1e-6)


def test_optimizer_rebalances_monthly_and_respects_limits() -> None:
    result = _engine().run(_matrix(), "optimizer")
    assert len(result.rebalances) >= 4
    for rebalance in result.rebalances:
        assert rebalance.drift_l1 >= 0.0
        assert rebalance.cost >= 0.0
        assert float(rebalance.weights.max()) <= 0.20 + 1e-8
        assert float(rebalance.weights.min()) >= 0.0


def test_large_threshold_avoids_trading() -> None:
    strategy = _strategy(rebalance_l1_threshold=10.0)
    result = _engine(strategy).run(_matrix(), "optimizer")
    assert all(rebalance.cost == 0.0 for rebalance in result.rebalances)


def test_transaction_costs_reduce_equity_for_equal_weight() -> None:
    free = _engine(_strategy(transaction_cost_lambda=0.0)).run(_matrix(), "equal_weight")
    costly = _engine(_strategy(transaction_cost_lambda=0.01)).run(_matrix(), "equal_weight")
    assert costly.equity[-1] < free.equity[-1]


def test_future_data_does_not_change_past_decisions() -> None:
    matrix = _matrix()
    baseline = _engine().run(matrix, "optimizer")
    cutoff_index = 80
    altered_values = matrix.values.copy()
    altered_values[cutoff_index + 1 :] *= 3.0
    altered = _engine().run(
        ReturnsMatrix(
            symbols=matrix.symbols,
            dates=matrix.dates,
            values=altered_values,
            excluded={},
        ),
        "optimizer",
    )
    cutoff = matrix.dates[cutoff_index]
    baseline_before = [item for item in baseline.rebalances if item.date <= cutoff]
    altered_before = [item for item in altered.rebalances if item.date <= cutoff]
    assert len(baseline_before) == len(altered_before)
    for left, right in zip(baseline_before, altered_before, strict=True):
        assert left.date == right.date
        assert np.array_equal(left.weights, right.weights)


def test_insufficient_history_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        _engine().run(_matrix(rows=40, window=30), "optimizer")


def test_too_few_assets_are_rejected() -> None:
    matrix = _matrix()
    reduced = ReturnsMatrix(
        symbols=matrix.symbols[:4],
        dates=matrix.dates,
        values=matrix.values[:, :4],
        excluded={},
    )
    with pytest.raises(DataValidationError):
        _engine().run(reduced, "optimizer")


def test_unsupported_mode_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        _engine().run(_matrix(), "buy_and_hold")
