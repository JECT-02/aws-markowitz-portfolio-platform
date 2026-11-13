from __future__ import annotations

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

from infra.config import DeployConfig
from infra.constructs.bucket_policy import secure_bucket
from infra.constructs.tagged import apply_tags
from infra.stacks.api_stack import ApiStack

WEB_DIR = "web"
CONFIG_KEY = "config.js"


class WebStack(Stack):
    """Static SPA hosting: private S3 bucket served through CloudFront."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: DeployConfig,
        api: ApiStack,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self._config = config
        apply_tags(self, config)

        self.bucket = secure_bucket(self, "WebBucket", versioned=True)
        self.distribution = cloudfront.Distribution(
            self,
            "SpaDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "*config.js": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                )
            },
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
            ],
        )
        s3deploy.BucketDeployment(
            self,
            "DeploySpa",
            sources=[s3deploy.Source.asset(WEB_DIR, exclude=[CONFIG_KEY])],
            destination_bucket=self.bucket,
            distribution=self.distribution,
            distribution_paths=["/*"],
        )
        s3deploy.DeployTimeSubstitutedFile(
            self,
            "SpaConfig",
            destination_bucket=self.bucket,
            source=f"{WEB_DIR}/{CONFIG_KEY}",
            destination_key=CONFIG_KEY,
            substitutions={
                "API_ENDPOINT": api.http_api.api_endpoint,
                "COGNITO_DOMAIN": f"https://{api.user_pool_domain.domain_name}",
                "CLIENT_ID": api.web_client.user_pool_client_id,
            },
        )
        self._api = api

        CfnOutput(self, "SpaUrl", value=f"https://{self.distribution.distribution_domain_name}")
        CfnOutput(self, "CognitoDomain", value=f"https://{api.user_pool_domain.domain_name}")
