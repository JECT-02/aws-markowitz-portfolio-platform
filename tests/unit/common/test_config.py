from pathlib import Path

import pytest

from common.config import (
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
