from __future__ import annotations

import math

import numpy as np

from common.errors import DataValidationError

TRADING_DAYS = 252
MAX_DRAWDOWN_FLOOR = 1e-12
WIPED_OUT_FLOOR = -1.0


def portfolio_log_returns(
    asset_log_returns: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Aggregate asset log returns into exact portfolio log returns.

    The portfolio simple return is the weighted average of asset simple
    returns; the log return is then taken so that equity compounds exactly.
    """
    matrix = np.asarray(asset_log_returns, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise DataValidationError("asset returns must be a non-empty matrix")
    vector = _as_weights(weights)
    if matrix.shape[1] != vector.size:
        raise DataValidationError("weights must match the number of assets")
    if not np.isfinite(matrix).all():
        raise DataValidationError("asset returns contain non-finite values")
    portfolio_simple = (np.expm1(matrix) @ vector).astype(float)
    if not np.isfinite(portfolio_simple).all():
        raise DataValidationError("portfolio returns contain non-finite values")
    if float(portfolio_simple.min()) <= WIPED_OUT_FLOOR:
        raise DataValidationError("portfolio value collapsed")
    return np.asarray(np.log1p(portfolio_simple), dtype=float)


def annualized_return(log_returns: np.ndarray) -> float:
    """Return the annualized arithmetic mean of daily log returns."""
    values = _as_series(log_returns)
    return float(values.mean() * TRADING_DAYS)


def annualized_volatility(log_returns: np.ndarray) -> float:
    """Return the annualized standard deviation of daily log returns."""
    values = _as_series(log_returns)
    if values.size < 2:
        raise DataValidationError("at least two observations are required")
    return float(values.std(ddof=1) * np.sqrt(TRADING_DAYS))


def sharpe_ratio(log_returns: np.ndarray, risk_free_rate_annual: float) -> float:
    """Return the annualized Sharpe ratio with an explicit annual risk-free rate."""
    values = _as_series(log_returns)
    if values.size < 2:
        raise DataValidationError("at least two observations are required")
    daily_volatility = float(values.std(ddof=1))
    if daily_volatility <= 0.0:
        raise DataValidationError("volatility must be positive to compute Sharpe")
    daily_risk_free = math.log1p(risk_free_rate_annual) / TRADING_DAYS
    excess = float(values.mean()) - daily_risk_free
    return float(excess / daily_volatility * np.sqrt(TRADING_DAYS))


def sortino_ratio(log_returns: np.ndarray, risk_free_rate_annual: float) -> float:
    """Return the annualized Sortino ratio using downside deviation."""
    values = _as_series(log_returns)
    daily_risk_free = math.log1p(risk_free_rate_annual) / TRADING_DAYS
    excess = values - daily_risk_free
    downside = excess[excess < 0.0]
    if downside.size == 0:
        raise DataValidationError("no downside observations to compute Sortino")
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))
    if downside_deviation <= 0.0:
        raise DataValidationError("downside deviation must be positive")
    return float(excess.mean() / downside_deviation * np.sqrt(TRADING_DAYS))


def max_drawdown(log_returns: np.ndarray) -> float:
    """Return the maximum drawdown as a positive fraction of peak equity."""
    values = _as_series(log_returns)
    equity = np.concatenate([[1.0], np.exp(np.cumsum(values))])
    peaks = np.maximum.accumulate(equity)
    drawdowns = 1.0 - equity / peaks
    return float(drawdowns.max())


def calmar_ratio(log_returns: np.ndarray) -> float:
    """Return the annualized return divided by the maximum drawdown."""
    drawdown = max_drawdown(log_returns)
    if drawdown <= MAX_DRAWDOWN_FLOOR:
        raise DataValidationError("maximum drawdown is too small to compute Calmar")
    return float(annualized_return(log_returns) / drawdown)


def herfindahl_index(weights: np.ndarray) -> float:
    """Return the Herfindahl concentration index of the weights."""
    values = _as_weights(weights)
    return float(np.sum(np.square(values)))


def turnover_l1(weights: np.ndarray, previous: np.ndarray) -> float:
    """Return the L1 rotation between two weight vectors."""
    current = _as_weights(weights)
    prior = _as_weights(previous)
    if current.shape != prior.shape:
        raise DataValidationError("weight vectors must have the same shape")
    return float(np.abs(current - prior).sum())


def turnover_one_way(weights: np.ndarray, previous: np.ndarray) -> float:
    """Return the one-way turnover, equal to half of the L1 rotation."""
    return turnover_l1(weights, previous) / 2.0


def _as_series(values: np.ndarray) -> np.ndarray:
    series = np.asarray(values, dtype=float)
    if series.ndim != 1 or series.size == 0:
        raise DataValidationError("expected a one-dimensional non-empty series")
    if not np.isfinite(series).all():
        raise DataValidationError("series contains non-finite values")
    return series


def _as_weights(weights: np.ndarray) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    if values.ndim != 1 or values.size == 0:
        raise DataValidationError("weights must be a one-dimensional non-empty array")
    if not np.isfinite(values).all():
        raise DataValidationError("weights contain non-finite values")
    return values
