from __future__ import annotations

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infra.stacks.api_stack import ApiStack
from infra.stacks.data_stack import DataStack
from tests.infra.helpers import deploy_config


def _template(spa_allowed_origin: str | None = None) -> Template:
    app = App()
    environment = Environment(account="123456789012", region="us-east-1")
    config = deploy_config(spa_allowed_origin=spa_allowed_origin)
    data = DataStack(app, "Data", config=config, env=environment)
    api = ApiStack(app, "Api", config=config, data=data, env=environment)
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


def test_user_pool_domain_uses_a_deterministic_prefix() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::Cognito::UserPoolDomain",
        {"Domain": "portfolio-markowitz-test"},
    )


def test_pkce_flow_is_enabled_only_with_a_spa_origin() -> None:
    template = _template("https://spa.example")
    client = template.find_resources("AWS::Cognito::UserPoolClient")
    for resource in client.values():
        properties = resource["Properties"]
        assert properties["AllowedOAuthFlows"] == ["code"]
        assert properties["AllowedOAuthFlowsUserPoolClient"] is True
        assert properties["CallbackURLs"] == ["https://spa.example"]
        assert properties["LogoutURLs"] == ["https://spa.example"]
        assert properties["AllowedOAuthScopes"] == ["openid", "email", "profile"]


def test_user_pool_is_retained() -> None:
    template = _template()
    template.has_resource(
        "AWS::Cognito::UserPool",
        {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    )


def test_reader_function_is_bounded_and_without_dlq() -> None:
    template = _template()
    functions = template.find_resources("AWS::Lambda::Function")
    reader = [
        resource["Properties"]
        for resource in functions.values()
        if resource["Properties"].get("MemorySize") == 256
    ]
    assert len(reader) == 1
    properties = reader[0]
    assert properties["PackageType"] == "Image"
    assert properties["Timeout"] == 10
    assert "DeadLetterConfig" not in properties
    environment = properties["Environment"]["Variables"]
    for name in ("GOLD_BUCKET", "STATE_TABLE", "CODE_VERSION"):
        assert name in environment


def test_reader_role_is_least_privilege() -> None:
    template = _template()
    policies = template.find_resources("AWS::IAM::Policy")
    statements = [
        statement
        for policy in policies.values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
    ]
    actions: set[str] = set()
    resources: set[str] = set()
    for statement in statements:
        raw_actions = statement["Action"]
        raw_resources = statement.get("Resource", [])
        actions.update(raw_actions if isinstance(raw_actions, list) else [raw_actions])
        if raw_resources:
            raw_resources = raw_resources if isinstance(raw_resources, list) else [raw_resources]
            resources.update(str(resource) for resource in raw_resources)
    assert {"s3:GetObject", "dynamodb:GetItem"} <= actions
    assert all(action != "*" for action in actions)
    assert all(resource != "*" for resource in resources)


def test_all_routes_require_the_jwt_authorizer() -> None:
    template = _template()
    template.resource_count_is("AWS::ApiGatewayV2::Route", 4)
    routes = template.find_resources("AWS::ApiGatewayV2::Route")
    route_keys = set()
    for resource in routes.values():
        properties = resource["Properties"]
        route_keys.add(properties["RouteKey"])
        assert properties["AuthorizationType"] == "JWT"
        assert "AuthorizerId" in properties
    assert route_keys == {
        "GET /proposal",
        "GET /metrics",
        "GET /monitor",
        "GET /backtest",
    }


def test_jwt_authorizer_uses_the_user_pool() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Authorizer",
        {
            "AuthorizerType": "JWT",
            "IdentitySource": ["$request.header.Authorization"],
            "JwtConfiguration": {
                "Audience": Match.any_value(),
                "Issuer": Match.any_value(),
            },
        },
    )


def test_api_throttling_is_bounded() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "DefaultRouteSettings": {
                "ThrottlingBurstLimit": 5,
                "ThrottlingRateLimit": 10,
            },
        },
    )


def test_cors_is_absent_without_a_spa_origin() -> None:
    template = _template()
    api = template.find_resources("AWS::ApiGatewayV2::Api")
    for resource in api.values():
        assert "CorsConfiguration" not in resource["Properties"]


def test_cors_is_restricted_to_the_configured_origin() -> None:
    template = _template("https://spa.example")
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {
            "CorsConfiguration": {
                "AllowOrigins": ["https://spa.example"],
                "AllowMethods": ["GET"],
                "AllowHeaders": ["Authorization"],
            },
        },
    )
