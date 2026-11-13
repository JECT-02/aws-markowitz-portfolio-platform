from __future__ import annotations

from collections.abc import Mapping, Sequence

from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from infra.constructs.tagged_function import LOG_RETENTION


def container_function(
    scope: Construct,
    construct_id: str,
    *,
    function_name: str,
    repository: ecr.IRepository,
    image_tag: str,
    command: Sequence[str],
    environment: Mapping[str, str],
    timeout: Duration,
    memory_size: int,
    dlq: sqs.IQueue,
    description: str,
) -> lambda_.DockerImageFunction:
    """Create a container Lambda with bounded retries, a DLQ and short log retention."""
    log_group = logs.LogGroup(
        scope,
        f"{construct_id}LogGroup",
        log_group_name=f"/aws/lambda/{function_name}",
        retention=LOG_RETENTION,
        removal_policy=RemovalPolicy.RETAIN,
    )
    return lambda_.DockerImageFunction(
        scope,
        construct_id,
        function_name=function_name,
        code=lambda_.DockerImageCode.from_ecr(
            repository,
            tag_or_digest=image_tag,
            cmd=list(command),
        ),
        environment=dict(environment),
        architecture=lambda_.Architecture.X86_64,
        timeout=timeout,
        memory_size=memory_size,
        dead_letter_queue=dlq,
        dead_letter_queue_enabled=True,
        retry_attempts=0,
        log_group=log_group,
        description=description,
    )
