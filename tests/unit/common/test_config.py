from pathlib import Path

import pytest

from common.config import (
    StorageSettings,
    StrategyConfig,
    UniverseConfig,
    load_strategy_config,
    load_universe_config,
)
from common.errors import ConfigurationError


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_universe_config_normalizes_tickers(tmp_path: Path) -> None:
    path = _write(tmp_path / "universe.yaml", "tickers:\n  - spy\n  - qqq\nmax_weight: 0.20\n")
    config = load_universe_config(path)
    assert config.tickers == ["SPY", "QQQ"]
    assert config.max_weight == pytest.approx(0.20)


def test_load_strategy_config_applies_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "strategy.yaml", "window_days: 252\n")
    config = load_strategy_config(path)
    assert config.window_days == 252
    assert config.max_forward_fill_days is None


def test_strategy_requires_forward_fill_days() -> None:
    with pytest.raises(ConfigurationError):
        StrategyConfig().require_forward_fill_days()
    assert StrategyConfig(max_forward_fill_days=5).require_forward_fill_days() == 5


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
