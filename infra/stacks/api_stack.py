from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as apigw_integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk.aws_apigatewayv2_authorizers import HttpUserPoolAuthorizer
from constructs import Construct

from infra.config import PROJECT_NAME, DeployConfig
from infra.constructs.container_function import container_function
from infra.constructs.tagged import apply_tags
from infra.stacks.data_stack import DataStack

READER_RATE_LIMIT = 10.0
READER_BURST_LIMIT = 5
READER_MEMORY_SIZE = 256
READER_TIMEOUT = Duration.seconds(10)
READER_ROUTES = ("/proposal", "/metrics", "/monitor", "/backtest")


class ApiStack(Stack):
    """Cognito authentication and the read-only HTTP API for the Reader."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: DeployConfig,
        data: DataStack,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self._config = config
        apply_tags(self, config)

        self.user_pool = cognito.UserPool(
            self,
            "ApiUserPool",
            user_pool_name=f"{PROJECT_NAME}-users-{config.env_name}",
            sign_in_aliases=cognito.SignInAliases(email=True),
            self_sign_up_enabled=False,
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.web_client = cognito.UserPoolClient(
            self,
            "WebClient",
            user_pool=self.user_pool,
            user_pool_client_name=f"{PROJECT_NAME}-spa-{config.env_name}",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                user_password=False,
                admin_user_password=False,
                custom=False,
            ),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    implicit_code_grant=False,
                    authorization_code_grant=False,
                ),
                scopes=[],
            ),
            prevent_user_existence_errors=True,
        )
        self.reader_repository = ecr.Repository(
            self,
            "ReaderRepository",
            repository_name=f"{PROJECT_NAME}-reader-{config.env_name}",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    rule_priority=1,
                    description="Expire untagged reader images",
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=Duration.days(14),
                )
            ],
        )
        self.reader = container_function(
            self,
            "ReaderFunction",
            function_name=f"{PROJECT_NAME}-reader-{config.env_name}",
            repository=self.reader_repository,
            image_tag=config.image_tag,
            command=["reader.handler.handler"],
            environment={
                "CODE_VERSION": config.code_version,
                "CONFIG_DIR": "/var/task",
                "BRONZE_BUCKET": data.bronze_bucket.bucket_name,
                "QUARANTINE_BUCKET": data.quarantine_bucket.bucket_name,
                "SILVER_BUCKET": data.silver_bucket.bucket_name,
                "GOLD_BUCKET": data.gold_bucket.bucket_name,
                "STATE_TABLE": data.state_table.table_name,
            },
            timeout=READER_TIMEOUT,
            memory_size=READER_MEMORY_SIZE,
            description="Read-only portfolio API",
        )
        self.reader.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    self.format_arn(
                        service="s3",
                        resource=f"{data.gold_bucket.bucket_name}/*",
                    )
                ],
            )
        )
        self.reader.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:GetItem"],
                resources=[data.state_table.table_arn],
            )
        )

        self.http_api = self._http_api(config)

        CfnOutput(self, "ApiEndpoint", value=self.http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        CfnOutput(self, "WebClientId", value=self.web_client.user_pool_client_id)
        CfnOutput(self, "ReaderRepositoryUri", value=self.reader_repository.repository_uri)

    def _http_api(self, config: DeployConfig) -> apigw.HttpApi:
        api = apigw.HttpApi(
            self,
            "ReaderHttpApi",
            api_name=f"{PROJECT_NAME}-reader-{config.env_name}",
            cors_preflight=(
                apigw.CorsPreflightOptions(
                    allow_origins=[config.spa_allowed_origin],
                    allow_methods=[apigw.CorsHttpMethod.GET],
                    allow_headers=["Authorization"],
                    max_age=Duration.hours(1),
                )
                if config.spa_allowed_origin is not None
                else None
            ),
        )
        self._throttle_default_stage(api)
        authorizer = HttpUserPoolAuthorizer(
            "JwtAuthorizer",
            self.user_pool,
            identity_source=["$request.header.Authorization"],
            user_pool_clients=[self.web_client],
        )
        integration = apigw_integrations.HttpLambdaIntegration(
            "ReaderIntegration",
            self.reader,
        )
        for path in READER_ROUTES:
            api.add_routes(
                path=path,
                methods=[apigw.HttpMethod.GET],
                integration=integration,
                authorizer=authorizer,
            )
        return api

    def _throttle_default_stage(self, api: apigw.HttpApi) -> None:
        stage = api.default_stage
        if stage is None:
            raise RuntimeError("http api did not create a default stage")
        default_child = stage.node.default_child
        if not isinstance(default_child, apigw.CfnStage):
            raise RuntimeError("default stage is not a raw CfnStage")
        default_child.default_route_settings = apigw.CfnStage.RouteSettingsProperty(
            throttling_burst_limit=READER_BURST_LIMIT,
            throttling_rate_limit=READER_RATE_LIMIT,
        )
