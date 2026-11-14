from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from common.errors import SecretError
from executor.adapters.secrets import load_alpaca_credentials

API_KEY_PARAMETER = "/portfolio-markowitz/dev/alpaca/api-key"
SECRET_KEY_PARAMETER = "/portfolio-markowitz/dev/alpaca/secret-key"


@mock_aws
def test_loads_both_credentials() -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    ssm.put_parameter(Name=API_KEY_PARAMETER, Type="SecureString", Value=" key-id ")
    ssm.put_parameter(Name=SECRET_KEY_PARAMETER, Type="SecureString", Value="secret")

    credentials = load_alpaca_credentials(
        api_key_parameter=API_KEY_PARAMETER,
        secret_key_parameter=SECRET_KEY_PARAMETER,
        region_name="us-east-1",
        ssm_client=ssm,
    )
    assert credentials.api_key == "key-id"
    assert credentials.secret_key == "secret"


@mock_aws
def test_missing_parameter_raises_secret_error() -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    ssm.put_parameter(Name=API_KEY_PARAMETER, Type="SecureString", Value="key-id")

    with pytest.raises(SecretError):
        load_alpaca_credentials(
            api_key_parameter=API_KEY_PARAMETER,
            secret_key_parameter=SECRET_KEY_PARAMETER,
            region_name="us-east-1",
            ssm_client=ssm,
        )


@mock_aws
def test_empty_value_raises_secret_error() -> None:
    ssm = boto3.client("ssm", region_name="us-east-1")
    ssm.put_parameter(Name=API_KEY_PARAMETER, Type="SecureString", Value="key-id")
    ssm.put_parameter(Name=SECRET_KEY_PARAMETER, Type="SecureString", Value="   ")

    with pytest.raises(SecretError):
        load_alpaca_credentials(
            api_key_parameter=API_KEY_PARAMETER,
            secret_key_parameter=SECRET_KEY_PARAMETER,
            region_name="us-east-1",
            ssm_client=ssm,
        )
