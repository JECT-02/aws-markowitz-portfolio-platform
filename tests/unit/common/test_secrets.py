from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from common.errors import ConfigurationError, SecretError
from common.secrets import SecretsSettings, SsmSecretProvider


class _FakeSsm:
    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = dict(values)
        self.calls: list[str] = []

    def get_parameter(self, **kwargs: object) -> dict[str, object]:
        name = str(kwargs["Name"])
        self.calls.append(name)
        if name not in self.values:
            raise ClientError(
                {"Error": {"Code": "ParameterNotFound", "Message": "missing"}},
                "GetParameter",
            )
        return {"Parameter": {"Name": name, "Value": self.values[name]}}


def test_provider_reads_and_caches_values() -> None:
    client = _FakeSsm({"key": "value"})
    provider = SsmSecretProvider(cast(BaseClient, client))
    assert provider.get_secret("key") == "value"
    assert provider.get_secret("key") == "value"
    assert client.calls == ["key"]


def test_provider_wraps_aws_errors() -> None:
    client = _FakeSsm({})
    provider = SsmSecretProvider(cast(BaseClient, client))
    with pytest.raises(SecretError):
        provider.get_secret("missing")


def test_provider_rejects_empty_values() -> None:
    client = _FakeSsm({"empty": ""})
    provider = SsmSecretProvider(cast(BaseClient, client))
    with pytest.raises(SecretError):
        provider.get_secret("empty")


def test_settings_require_parameter_names() -> None:
    settings = SecretsSettings.from_environment(
        {
            "ALPACA_API_KEY_PARAM": "/portfolio/alpaca/key",
            "ALPACA_SECRET_KEY_PARAM": "/portfolio/alpaca/secret",
        }
    )
    assert settings.api_key_parameter == "/portfolio/alpaca/key"
    assert settings.secret_key_parameter == "/portfolio/alpaca/secret"


def test_settings_reject_missing_names() -> None:
    with pytest.raises(ConfigurationError):
        SecretsSettings.from_environment({})
