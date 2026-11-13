from __future__ import annotations

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from constructs import Construct

from infra.config import PROJECT_NAME, DeployConfig
from infra.constructs.tagged import apply_tags


class ApiStack(Stack):
    """Cognito authentication for the read-only portfolio API."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: DeployConfig,
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
