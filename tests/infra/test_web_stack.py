from __future__ import annotations

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infra.stacks.api_stack import ApiStack
from infra.stacks.data_stack import DataStack
from infra.stacks.web_stack import WebStack
from tests.infra.helpers import deploy_config


def _template() -> Template:
    app = App()
    environment = Environment(account="123456789012", region="us-east-1")
    config = deploy_config()
    data = DataStack(app, "Data", config=config, env=environment)
    api = ApiStack(app, "Api", config=config, data=data, env=environment)
    web = WebStack(app, "Web", config=config, api=api, env=environment)
    return Template.from_stack(web)


def test_web_bucket_is_private_and_versioned() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": Match.object_like(
                {"ServerSideEncryptionConfiguration": Match.any_value()}
            ),
        },
    )
    template.has_resource(
        "AWS::S3::Bucket",
        {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    )


def test_distribution_enforces_https_through_oac() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "DefaultRootObject": "index.html",
                "DefaultCacheBehavior": {
                    "ViewerProtocolPolicy": "redirect-to-https",
                },
            },
        },
    )
    template.resource_count_is("AWS::CloudFront::OriginAccessControl", 2)


def test_unknown_paths_render_the_spa() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "CustomErrorResponses": [
                    {
                        "ErrorCode": 403,
                        "ResponseCode": 200,
                        "ResponsePagePath": "/index.html",
                    },
                    {
                        "ErrorCode": 404,
                        "ResponseCode": 200,
                        "ResponsePagePath": "/index.html",
                    },
                ],
            },
        },
    )


def test_spa_assets_are_deployed() -> None:
    template = _template()
    deployments = template.find_resources("Custom::CDKBucketDeployment")
    assert len(deployments) == 2
    asset_deployment = [
        resource["Properties"]
        for resource in deployments.values()
        if "DistributionId" in resource["Properties"]
    ]
    assert len(asset_deployment) == 1
    assert asset_deployment[0]["DistributionPaths"] == ["/*"]


def test_spa_config_uses_deploy_time_substitutions() -> None:
    template = _template()
    deployments = template.find_resources("Custom::CDKBucketDeployment")
    config_deployment = [
        resource["Properties"]
        for resource in deployments.values()
        if "SourceMarkers" in resource["Properties"]
    ]
    assert len(config_deployment) == 1
    assert len(config_deployment[0]["SourceMarkers"][0]) == 3
    template.resource_count_is("AWS::Lambda::LayerVersion", 2)
