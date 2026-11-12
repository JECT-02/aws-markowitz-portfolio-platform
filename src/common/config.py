from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.errors import ConfigurationError

_STORAGE_ENV = {
    "bronze_bucket": "BRONZE_BUCKET",
    "quarantine_bucket": "QUARANTINE_BUCKET",
    "silver_bucket": "SILVER_BUCKET",
    "gold_bucket": "GOLD_BUCKET",
}


class UniverseConfig(BaseModel):
    """Validated universe definition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: list[str] = Field(min_length=1)
    max_weight: float = Field(gt=0.0, le=1.0)

    @field_validator("tickers")
    @classmethod
    def _validate_tickers(cls, value: list[str]) -> list[str]:
        normalized = [ticker.strip().upper() for ticker in value]
        if any(not ticker for ticker in normalized):
            raise ValueError("tickers must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("tickers must be unique")
        return normalized


class StrategyConfig(BaseModel):
    """Validated strategy parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_aversion: float | None = Field(default=None, gt=0.0)
    transaction_cost_lambda: float | None = Field(default=None, ge=0.0)
    return_shrinkage_alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    window_days: int = Field(default=252, ge=2)
    rebalance_l1_threshold: float = Field(default=0.10, gt=0.0)
    risk_free_rate_annual: float | None = None
    max_forward_fill_days: int | None = Field(default=None, ge=0)

    def require_forward_fill_days(self) -> int:
        """Return the configured forward-fill limit or raise if unset."""
        if self.max_forward_fill_days is None:
            raise ConfigurationError("max_forward_fill_days is required")
        return self.max_forward_fill_days


class StorageSettings(BaseModel):
    """Bucket names resolved from the runtime environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bronze_bucket: str = Field(min_length=1)
    quarantine_bucket: str = Field(min_length=1)
    silver_bucket: str = Field(min_length=1)
    gold_bucket: str = Field(min_length=1)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> StorageSettings:
        """Build storage settings from environment variables."""
        source = os.environ if environ is None else environ
        values: dict[str, str] = {}
        missing: list[str] = []
        for field_name, env_name in _STORAGE_ENV.items():
            value = source.get(env_name)
            if value is None or not value.strip():
                missing.append(env_name)
            else:
                values[field_name] = value
        if missing:
            raise ConfigurationError("missing environment variables: " + ", ".join(sorted(missing)))
        return cls.model_validate(values)


def _read_yaml_mapping(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ConfigurationError(f"configuration file not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid yaml: {path}") from exc
    if not isinstance(document, dict):
        raise ConfigurationError(f"configuration must be a mapping: {path}")
    return {str(key): value for key, value in document.items()}


def load_universe_config(path: Path) -> UniverseConfig:
    """Load and validate the universe configuration file."""
    document = _read_yaml_mapping(path)
    try:
        return UniverseConfig.model_validate(document)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid universe configuration: {path}") from exc


def load_strategy_config(path: Path) -> StrategyConfig:
    """Load and validate the strategy configuration file."""
    document = _read_yaml_mapping(path)
    try:
        return StrategyConfig.model_validate(document)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid strategy configuration: {path}") from exc
