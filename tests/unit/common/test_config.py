from pathlib import Path

import pytest

from common.config import (
    ExecutorConfig,
    RuntimeSettings,
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_universe_config,
)
from common.errors import ConfigurationError


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _strategy_env() -> dict[str, str]:
    return {
        "RISK_AVERSION": "2.5",
        "TRANSACTION_COST_LAMBDA": "0.001",
        "RETURN_SHRINKAGE_ALPHA": "0.25",
        "RISK_FREE_RATE_ANNUAL": "0.04",
        "MAX_FORWARD_FILL_DAYS": "3",
        "WINDOW_DAYS": "252",
        "INGEST_LOOKBACK_DAYS": "420",
        "REBALANCE_L1_THRESHOLD": "0.10",
        "VALIDATION_GAMMAS": "1,2.5,5",
        "VALIDATION_LAMBDAS": "0,0.001",
        "VALIDATION_ALPHAS": "0,0.25,0.5",
        "VALIDATION_WINDOWS": "126,252,504",
        "VALIDATION_FOLDS": "3",
    }


def test_load_universe_config_normalizes_tickers(tmp_path: Path) -> None:
    path = _write(tmp_path / "universe.yaml", "tickers:\n  - spy\n  - qqq\nmax_weight: 0.20\n")
    config = load_universe_config(path)
    assert config.tickers == ["SPY", "QQQ"]
    assert config.max_weight == pytest.approx(0.20)


def test_strategy_config_from_environment() -> None:
    config = StrategyConfig.from_environment(_strategy_env())
    assert config.risk_aversion == pytest.approx(2.5)
    assert config.max_forward_fill_days == 3
    assert config.validation_gammas == [1.0, 2.5, 5.0]
    assert config.validation_windows == [126, 252, 504]
    assert config.validation_folds == 3


def test_strategy_config_requires_every_variable() -> None:
    environ = _strategy_env()
    del environ["RISK_FREE_RATE_ANNUAL"]
    with pytest.raises(ConfigurationError) as error:
        StrategyConfig.from_environment(environ)
    assert "RISK_FREE_RATE_ANNUAL" in str(error.value)


def test_strategy_config_rejects_invalid_values() -> None:
    environ = _strategy_env()
    environ["RETURN_SHRINKAGE_ALPHA"] = "2"
    with pytest.raises(ConfigurationError):
        StrategyConfig.from_environment(environ)


def test_strategy_config_rejects_empty_list() -> None:
    environ = _strategy_env()
    environ["VALIDATION_GAMMAS"] = " , "
    with pytest.raises(ConfigurationError):
        StrategyConfig.from_environment(environ)


def test_missing_file_raises_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_universe_config(tmp_path / "missing.yaml")


def test_invalid_universe_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "universe.yaml", "tickers: []\nmax_weight: 0.20\n")
    with pytest.raises(ConfigurationError):
        load_universe_config(path)


def test_duplicate_tickers_are_rejected() -> None:
    with pytest.raises(ValueError):
        UniverseConfig(tickers=["SPY", "spy"], max_weight=0.20)


def test_storage_settings_from_mapping() -> None:
    settings = StorageSettings.from_environment(
        {
            "BRONZE_BUCKET": "bronze",
            "QUARANTINE_BUCKET": "quarantine",
            "SILVER_BUCKET": "silver",
            "GOLD_BUCKET": "gold",
        }
    )
    assert settings.bronze_bucket == "bronze"
    assert settings.gold_bucket == "gold"


def test_storage_settings_missing_variables() -> None:
    with pytest.raises(ConfigurationError) as error:
        StorageSettings.from_environment({"BRONZE_BUCKET": "bronze"})
    assert "QUARANTINE_BUCKET" in str(error.value)


def test_executor_config_from_environment() -> None:
    config = ExecutorConfig.from_environment(_executor_env())
    assert config.min_order_notional == pytest.approx(50.0)
    assert config.max_price_drift_pct == pytest.approx(0.01)
    assert config.execution_timeout_seconds == 60


def test_executor_config_requires_every_variable() -> None:
    environ = _executor_env()
    del environ["EXECUTOR_MAX_PRICE_DRIFT_PCT"]
    with pytest.raises(ConfigurationError) as error:
        ExecutorConfig.from_environment(environ)
    assert "EXECUTOR_MAX_PRICE_DRIFT_PCT" in str(error.value)


def test_executor_config_rejects_invalid_values() -> None:
    environ = _executor_env()
    environ["EXECUTOR_MAX_PRICE_DRIFT_PCT"] = "2"
    with pytest.raises(ConfigurationError):
        ExecutorConfig.from_environment(environ)


def _executor_env() -> dict[str, str]:
    return {
        "EXECUTOR_MIN_ORDER_NOTIONAL": "50",
        "EXECUTOR_MAX_PRICE_DRIFT_PCT": "0.01",
        "EXECUTOR_EXECUTION_TIMEOUT_SECONDS": "60",
    }


def _runtime_env() -> dict[str, str]:
    return {
        "AWS_REGION": "us-east-1",
        "CODE_VERSION": "abc1234",
        "CONFIG_DIR": "config",
    }


def test_runtime_settings_from_environment() -> None:
    settings = RuntimeSettings.from_environment(_runtime_env())
    assert settings.aws_region == "us-east-1"
    assert settings.code_version == "abc1234"
    assert settings.config_dir == Path("config")


@pytest.mark.parametrize("name", ["AWS_REGION", "CODE_VERSION", "CONFIG_DIR"])
def test_runtime_settings_requires_every_variable(name: str) -> None:
    environ = _runtime_env()
    del environ[name]
    with pytest.raises(ConfigurationError) as error:
        RuntimeSettings.from_environment(environ)
    assert name in str(error.value)
