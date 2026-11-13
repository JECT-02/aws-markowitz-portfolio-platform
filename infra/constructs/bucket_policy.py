from __future__ import annotations

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_s3 as s3
from constructs import Construct


def secure_bucket(
    scope: Construct,
    construct_id: str,
    *,
    versioned: bool,
    expiration_days: int | None = None,
) -> s3.Bucket:
    """Create a private, encrypted bucket with TLS enforced and data retained."""
    lifecycle_rules = None
    if expiration_days is not None:
        lifecycle_rules = [
            s3.LifecycleRule(
                expiration=Duration.days(expiration_days),
                noncurrent_version_expiration=(
                    Duration.days(expiration_days) if versioned else None
                ),
            )
        ]
    return s3.Bucket(
        scope,
        construct_id,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        encryption=s3.BucketEncryption.S3_MANAGED,
        enforce_ssl=True,
        versioned=versioned,
        auto_delete_objects=False,
        removal_policy=RemovalPolicy.RETAIN,
        lifecycle_rules=lifecycle_rules,
        object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
    )
