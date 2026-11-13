import math

import numpy as np
import pytest

from common.errors import DataValidationError
from optimizer.core.metrics import (
    TRADING_DAYS,
    annualized_return,
    annualized_volatility,
    calmar_ratio,
    herfindahl_index,
    max_drawdown,
    portfolio_log_returns,
    sharpe_ratio,
    sortino_ratio,
    turnover_l1,
    turnover_one_way,
)


def test_portfolio_log_returns_single_asset_is_exact() -> None:
    asset = np.array([[0.01], [-0.02], [0.03]])
    portfolio = portfolio_log_returns(asset, np.array([1.0]))
    assert np.allclose(portfolio, asset[:, 0])


def test_portfolio_log_returns_weighted_average_in_simple_space() -> None:
    asset = np.array([[math.log(1.10), math.log(0.90)]])
    weights = np.array([0.5, 0.5])
    portfolio = portfolio_log_returns(asset, weights)
    expected = math.log(0.5 * 1.10 + 0.5 * 0.90)
    assert float(portfolio[0]) == pytest.approx(expected)


def test_portfolio_log_returns_rejects_shape_mismatch() -> None:
    with pytest.raises(DataValidationError):
        portfolio_log_returns(np.zeros((3, 2)), np.array([1.0]))


def test_portfolio_log_returns_rejects_collapsed_portfolio() -> None:
    asset = np.array([[-40.0]])
    with pytest.raises(DataValidationError):
        portfolio_log_returns(asset, np.array([1.0]))


def test_annualized_return() -> None:
    returns = np.full(TRADING_DAYS, 0.01)
    assert annualized_return(returns) == pytest.approx(0.01 * TRADING_DAYS)


def test_annualized_volatility() -> None:
    returns = np.array([0.01, 0.02])
    expected = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
    assert annualized_volatility(returns) == pytest.approx(expected)


def test_sharpe_ratio_with_zero_risk_free() -> None:
    returns = np.array([0.01, 0.02, -0.01, 0.005])
    expected = float(returns.mean() / returns.std(ddof=1) * np.sqrt(TRADING_DAYS))
    assert sharpe_ratio(returns, 0.0) == pytest.approx(expected)


def test_sharpe_ratio_subtracts_daily_log_risk_free_rate() -> None:
    returns = np.array([0.01, 0.02, -0.01, 0.005])
    daily_risk_free = math.log1p(0.04) / TRADING_DAYS
    expected = float(
        (returns.mean() - daily_risk_free) / returns.std(ddof=1) * np.sqrt(TRADING_DAYS)
    )
    assert sharpe_ratio(returns, 0.04) == pytest.approx(expected)


def test_sortino_ratio_uses_downside_deviation() -> None:
    returns = np.array([0.02, -0.01, 0.03, -0.02])
    downside = np.array([-0.01, -0.02])
    expected = float(returns.mean() / np.sqrt(np.mean(downside**2)) * np.sqrt(TRADING_DAYS))
    assert sortino_ratio(returns, 0.0) == pytest.approx(expected)


def test_max_drawdown() -> None:
    returns = np.array([-0.10, -0.10])
    expected = 1.0 - float(np.exp(-0.20))
    assert max_drawdown(returns) == pytest.approx(expected)


def test_calmar_ratio() -> None:
    returns = np.array([0.02, -0.01, 0.01, -0.02, 0.03])
    expected = annualized_return(returns) / max_drawdown(returns)
    assert calmar_ratio(returns) == pytest.approx(expected)


def test_herfindahl_and_turnover() -> None:
    assert herfindahl_index(np.array([0.5, 0.5])) == pytest.approx(0.5)
    assert turnover_l1(np.array([0.6, 0.4]), np.array([0.5, 0.5])) == pytest.approx(0.2)
    assert turnover_one_way(np.array([0.6, 0.4]), np.array([0.5, 0.5])) == pytest.approx(0.1)


def test_empty_series_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        annualized_return(np.array([]))


def test_non_finite_series_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        annualized_volatility(np.array([0.01, np.nan]))


def test_zero_volatility_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        sharpe_ratio(np.array([0.01, 0.01]), 0.0)


def test_no_downside_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        sortino_ratio(np.array([0.01, 0.02]), 0.0)


def test_zero_drawdown_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        calmar_ratio(np.array([0.01, 0.02]))


def test_turnover_shape_mismatch_is_rejected() -> None:
    with pytest.raises(DataValidationError):
        turnover_l1(np.array([1.0]), np.array([0.5, 0.5]))
