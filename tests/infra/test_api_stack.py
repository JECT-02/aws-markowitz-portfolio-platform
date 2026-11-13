from __future__ import annotations

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from infra.stacks.api_stack import ApiStack
from tests.infra.helpers import deploy_config


def _template() -> Template:
    app = App()
    environment = Environment(account="123456789012", region="us-east-1")
    api = ApiStack(app, "Api", config=deploy_config(), env=environment)
    return Template.from_stack(api)


def test_user_pool_disallows_sign_up_and_recommends_mfa() -> None:
    template = _template()
    pool = template.find_resources("AWS::Cognito::UserPool")
    assert len(pool) == 1
    for resource in pool.values():
        properties = resource["Properties"]
        assert properties.get("SelfSignUpEnabled", False) is False
        assert properties["MfaConfiguration"] == "OPTIONAL"
        assert "SOFTWARE_TOKEN_MFA" in properties["EnabledMfas"]
        policy = properties["Policies"]["PasswordPolicy"]
        assert policy["MinimumLength"] == 12
        for flag in (
            "RequireNumbers",
            "RequireLowercase",
            "RequireUppercase",
            "RequireSymbols",
        ):
            assert policy[flag] is True


def test_web_client_is_public_and_uses_srp_only() -> None:
    template = _template()
    client = template.find_resources("AWS::Cognito::UserPoolClient")
    assert len(client) == 1
    for resource in client.values():
        properties = resource["Properties"]
        assert properties.get("GenerateSecret", False) is False
        assert properties["ExplicitAuthFlows"] == [
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ]
        assert "AllowedOAuthFlows" not in properties
        assert "CallbackURLs" not in properties
        assert properties["PreventUserExistenceErrors"] == "ENABLED"


def test_user_pool_is_retained() -> None:
    template = _template()
    template.has_resource(
        "AWS::Cognito::UserPool",
        {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    )
