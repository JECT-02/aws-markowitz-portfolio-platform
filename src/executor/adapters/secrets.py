from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import SecretError


@dataclass(frozen=True)
class AlpacaCredentials:
    """Alpaca paper trading credentials resolved from SSM SecureStrings."""

    api_key: str
    secret_key: str


def load_alpaca_credentials(
    api_key_parameter: str,
    secret_key_parameter: str,
    region_name: str,
    ssm_client: Any | None = None,
) -> AlpacaCredentials:
    """Read the two Alpaca SecureString parameters at runtime.

    Preconditions: the human stored both parameter values beforehand.
    Postconditions: values are non-empty; they are never logged.
    """
    client = ssm_client if ssm_client is not None else boto3.client("ssm", region_name=region_name)
    return AlpacaCredentials(
        api_key=_read_parameter(client, api_key_parameter),
        secret_key=_read_parameter(client, secret_key_parameter),
    )


def _read_parameter(client: Any, name: str) -> str:
    try:
        response = client.get_parameter(Name=name, WithDecryption=True)
    except (ClientError, BotoCoreError) as exc:
        raise SecretError(f"cannot read the ssm parameter {name}") from exc
    value = response.get("Parameter", {}).get("Value")
    if not isinstance(value, str) or not value.strip():
        raise SecretError(f"the ssm parameter {name} is empty")
    return value.strip()
