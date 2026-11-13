from __future__ import annotations

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infra.stacks.data_stack import DataStack
from tests.infra.helpers import deploy_config


def _template(*, notification_email: str | None = None) -> Template:
    app = App()
    stack = DataStack(
        app,
        "Data",
        config=deploy_config(notification_email=notification_email),
        env=Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


def test_buckets_are_private_encrypted_and_retained() -> None:
    template = _template()
    template.resource_count_is("AWS::S3::Bucket", 4)
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
        },
    )
    template.has_resource(
        "AWS::S3::Bucket",
        {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    )
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {"VersioningConfiguration": {"Status": "Enabled"}},
    )


def test_bucket_policies_enforce_tls() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Deny",
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        )
                    ]
                )
            }
        },
    )


def test_state_table_is_provisioned_without_pitr() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "ProvisionedThroughput": {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1},
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": False},
            "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True},
        },
    )
    template.has_resource(
        "AWS::DynamoDB::Table",
        {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
    )


def test_dead_letter_queues_are_encrypted() -> None:
    template = _template()
    template.resource_count_is("AWS::SQS::Queue", 5)
    template.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "SqsManagedSseEnabled": True,
            "MessageRetentionPeriod": 345600,
        },
    )


def test_notification_topic_enforces_tls() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::SNS::Topic",
        {"DisplayName": "Portfolio Markowitz notifications"},
    )
    template.has_resource_properties(
        "AWS::SNS::TopicPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Deny",
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        )
                    ]
                )
            }
        },
    )


def test_email_subscription_is_optional() -> None:
    without_email = _template()
    without_email.resource_count_is("AWS::SNS::Subscription", 0)
    with_email = _template(notification_email="ops@example.com")
    with_email.resource_count_is("AWS::SNS::Subscription", 1)
    with_email.has_resource_properties(
        "AWS::SNS::Subscription",
        {"Protocol": "email", "Endpoint": "ops@example.com"},
    )


def test_all_resources_carry_mandatory_tags() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "Tags": Match.array_with(
                [
                    {"Key": "cost-center", "Value": "university"},
                    {"Key": "env", "Value": "test"},
                    {"Key": "owner", "Value": "portfolio-team"},
                    {"Key": "project", "Value": "portfolio-markowitz"},
                ]
            )
        },
    )
