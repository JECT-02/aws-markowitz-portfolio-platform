from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from common.errors import ConfigurationError, SecretError
from executor.handler import _build_broker

API_KEY_PARAMETER = "/portfolio-markowitz/dev/alpaca/api-key"
SECRET_KEY_PARAMETER = "/portfolio-markowitz/dev/alpaca/secret-key"


@mock_aws
def _put_parameters() -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    ssm.put_parameter(
        Name=API_KEY_PARAMETER,
        Type="SecureString",
        Value="paper-key-id",
        Tier="Standard",
    )
    ssm.put_parameter(
        Name=SECRET_KEY_PARAMETER,
        Type="SecureString",
        Value="paper-secret",
        Tier="Standard",
    )


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "ALPACA_PAPER": "true",
        "ALPACA_API_KEY_PARAMETER": API_KEY_PARAMETER,
        "ALPACA_SECRET_KEY_PARAMETER": SECRET_KEY_PARAMETER,
        "AWS_REGION": "us-east-1",
        "CODE_VERSION": "test",
        "CONFIG_DIR": "config",
    }
    values.update(overrides)
    return values


@mock_aws
def test_build_broker_loads_credentials_from_ssm() -> None:
    _put_parameters()
    with pytest.MonkeyPatch.context() as patcher:
        for name, value in _environment().items():
            patcher.setenv(name, value)
        broker = _build_broker(_runtime(patcher))
        assert type(broker).__name__ == "AlpacaPaperBroker"


@mock_aws
def test_missing_ssm_parameter_fails_with_secret_error() -> None:
    with pytest.MonkeyPatch.context() as patcher:
        for name, value in _environment().items():
            patcher.setenv(name, value)
        with pytest.raises(SecretError):
            _build_broker(_runtime(patcher))


def test_paper_flag_false_is_rejected_before_any_aws_call() -> None:
    with pytest.MonkeyPatch.context() as patcher:
        for name, value in _environment(ALPACA_PAPER="false").items():
            patcher.setenv(name, value)
        with pytest.raises(ConfigurationError, match="live trading is forbidden"):
            _build_broker(_runtime(patcher))


def test_missing_parameter_paths_are_rejected() -> None:
    with pytest.MonkeyPatch.context() as patcher:
        for name, value in _environment(ALPACA_API_KEY_PARAMETER="").items():
            patcher.setenv(name, value)
        with pytest.raises(ConfigurationError, match="ALPACA_API_KEY_PARAMETER"):
            _build_broker(_runtime(patcher))


def _runtime(patcher: pytest.MonkeyPatch) -> Any:
    from common.config import RuntimeSettings

    return RuntimeSettings.from_environment()
