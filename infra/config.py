from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from common.errors import ConfigurationError

PROJECT_NAME = "portfolio-markowitz"

STRATEGY_ENVIRONMENT = {
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
    "validation_folds": "VALIDATION_FOLDS",
}

DEFAULT_API_KEY_PARAMETER = "/portfolio-markowitz/dev/alpaca/api-key"
DEFAULT_SECRET_KEY_PARAMETER = "/portfolio-markowitz/dev/alpaca/secret-key"


@dataclass(frozen=True)
class DeployConfig:
    """Deployment settings resolved from files and environment variables."""

    env_name: str
    owner: str
    cost_center: str
    code_version: str
    image_tag: str
    notification_email: str | None
    api_key_parameter: str
    secret_key_parameter: str
    strategy_environment: Mapping[str, str]


def load_deploy_config(
    config_dir: Path,
    environ: Mapping[str, str] | None = None,
) -> DeployConfig:
    """Build the deployment configuration without embedding secrets."""
    source = os.environ if environ is None else environ
    env_name = _optional(source, "DEPLOY_ENV", "dev")
    return DeployConfig(
        env_name=env_name,
        owner=_optional(source, "OWNER_TAG", "portfolio-team"),
        cost_center=_optional(source, "COST_CENTER_TAG", "university"),
        code_version=_optional(source, "CODE_VERSION", "local"),
        image_tag=_optional(source, "OPTIMIZER_IMAGE_TAG", "latest"),
        notification_email=_blank_to_none(source.get("NOTIFICATION_EMAIL")),
        api_key_parameter=_optional(
            source,
            "ALPACA_API_KEY_PARAM",
            DEFAULT_API_KEY_PARAMETER,
        ),
        secret_key_parameter=_optional(
            source,
            "ALPACA_SECRET_KEY_PARAM",
            DEFAULT_SECRET_KEY_PARAMETER,
        ),
        strategy_environment=load_strategy_environment(config_dir),
    )


def load_strategy_environment(config_dir: Path) -> dict[str, str]:
    """Translate the strategy YAML into Lambda environment variables."""
    path = config_dir / "strategy.yaml"
    if not path.is_file():
        raise ConfigurationError(f"configuration file not found: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid yaml: {path}") from exc
    if document is None:
        return {}
    if not isinstance(document, dict):
        raise ConfigurationError(f"configuration must be a mapping: {path}")
    environment: dict[str, str] = {}
    for field_name, env_name in STRATEGY_ENVIRONMENT.items():
        value = document.get(field_name)
        if value is None:
            continue
        environment[env_name] = _render_value(value)
    return environment


def _render_value(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _optional(source: Mapping[str, str], name: str, default: str) -> str:
    value = source.get(name)
    return default if value is None or not value.strip() else value.strip()


def _blank_to_none(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value.strip()
