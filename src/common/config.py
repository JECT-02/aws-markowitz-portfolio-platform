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

_STRATEGY_ENV = {
    "risk_aversion": "RISK_AVERSION",
    "transaction_cost_lambda": "TRANSACTION_COST_LAMBDA",
    "return_shrinkage_alpha": "RETURN_SHRINKAGE_ALPHA",
    "risk_free_rate_annual": "RISK_FREE_RATE_ANNUAL",
    "max_forward_fill_days": "MAX_FORWARD_FILL_DAYS",
    "window_days": "WINDOW_DAYS",
    "ingest_lookback_days": "INGEST_LOOKBACK_DAYS",
    "rebalance_l1_threshold": "REBALANCE_L1_THRESHOLD",
    "validation_gammas": "VALIDATION_GAMMAS",
    "validation_lambdas": "VALIDATION_LAMBDAS",
    "validation_alphas": "VALIDATION_ALPHAS",
    "validation_windows": "VALIDATION_WINDOWS",
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
    """Validated strategy parameters resolved from the environment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_aversion: float = Field(gt=0.0)
    transaction_cost_lambda: float = Field(ge=0.0)
    return_shrinkage_alpha: float = Field(ge=0.0, le=1.0)
    risk_free_rate_annual: float
    max_forward_fill_days: int = Field(ge=0)
    window_days: int = Field(ge=2)
    ingest_lookback_days: int = Field(ge=2)
    rebalance_l1_threshold: float = Field(gt=0.0)
    validation_gammas: list[float] = Field(min_length=1)
    validation_lambdas: list[float] = Field(min_length=1)
    validation_alphas: list[float] = Field(min_length=1)
    validation_windows: list[int] = Field(min_length=1)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> StrategyConfig:
        """Build strategy parameters from required environment variables."""
        source = os.environ if environ is None else environ
        raw: dict[str, str] = {}
        for field_name, env_name in _STRATEGY_ENV.items():
            value = source.get(env_name)
            if value is None or not value.strip():
                raise ConfigurationError(f"missing environment variable: {env_name}")
            raw[field_name] = value.strip()
        try:
            return cls.model_validate(
                {
                    "risk_aversion": float(raw["risk_aversion"]),
                    "transaction_cost_lambda": float(raw["transaction_cost_lambda"]),
                    "return_shrinkage_alpha": float(raw["return_shrinkage_alpha"]),
                    "risk_free_rate_annual": float(raw["risk_free_rate_annual"]),
                    "max_forward_fill_days": int(raw["max_forward_fill_days"]),
                    "window_days": int(raw["window_days"]),
                    "ingest_lookback_days": int(raw["ingest_lookback_days"]),
                    "rebalance_l1_threshold": float(raw["rebalance_l1_threshold"]),
                    "validation_gammas": _parse_float_list(raw["validation_gammas"]),
                    "validation_lambdas": _parse_float_list(raw["validation_lambdas"]),
                    "validation_alphas": _parse_float_list(raw["validation_alphas"]),
                    "validation_windows": _parse_int_list(raw["validation_windows"]),
                }
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise ConfigurationError("invalid strategy environment variables") from exc



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


def _parse_float_list(value: str) -> list[float]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        raise ConfigurationError("validation list must not be empty")
    return [float(part) for part in parts]


def _parse_int_list(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        raise ConfigurationError("validation list must not be empty")
    return [int(part) for part in parts]
