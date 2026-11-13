from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from common.env import require_variable
from common.errors import SecretError


class SecretProvider(Protocol):
    """Reads secrets by name without exposing their values."""

    def get_secret(self, name: str) -> str: ...


class SsmSecretProvider:
    """Reads SSM SecureString parameters and caches them in memory."""

    def __init__(self, client: BaseClient | None = None, region_name: str | None = None) -> None:
        self._client: Any = (
            client if client is not None else boto3.client("ssm", region_name=region_name)
        )
        self._cache: dict[str, str] = {}

    def get_secret(self, name: str) -> str:
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        try:
            response = self._client.get_parameter(Name=name, WithDecryption=True)
        except (BotoCoreError, ClientError) as exc:
            raise SecretError(f"failed to read parameter: {name}") from exc
        parameter = response.get("Parameter", {})
        value = parameter.get("Value")
        if not isinstance(value, str) or not value:
            raise SecretError(f"parameter has no value: {name}")
        self._cache[name] = value
        return value


@dataclass(frozen=True)
class SecretsSettings:
    """SSM parameter names required by the ingest stage."""

    api_key_parameter: str
    secret_key_parameter: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> SecretsSettings:
        source = os.environ if environ is None else environ
        return cls(
            api_key_parameter=require_variable(source, "ALPACA_API_KEY_PARAM"),
            secret_key_parameter=require_variable(source, "ALPACA_SECRET_KEY_PARAM"),
        )
