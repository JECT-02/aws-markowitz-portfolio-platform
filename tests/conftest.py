from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from common.config import StrategyConfig


def _strategy_values() -> dict[str, Any]:
    return {
        "risk_aversion": 2.0,
        "transaction_cost_lambda": 0.001,
        "return_shrinkage_alpha": 0.5,
        "risk_free_rate_annual": 0.04,
        "max_forward_fill_days": 2,
        "window_days": 252,
        "ingest_lookback_days": 420,
        "rebalance_l1_threshold": 0.10,
        "validation_gammas": [1.0, 2.0],
        "validation_lambdas": [0.0, 0.001],
        "validation_alphas": [0.0, 0.5],
        "validation_windows": [126, 252],
        "validation_folds": 3,
    }


@pytest.fixture
def make_strategy_config() -> Callable[..., StrategyConfig]:
    def factory(**overrides: Any) -> StrategyConfig:
        values = _strategy_values()
        values.update(overrides)
        return StrategyConfig.model_validate(values)

    return factory
