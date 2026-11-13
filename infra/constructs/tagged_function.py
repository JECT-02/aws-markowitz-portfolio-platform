from __future__ import annotations

from collections.abc import Mapping

from aws_cdk import Duration, RemovalPolicy, Size
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sqs as sqs
from constructs import Construct

PYTHON_RUNTIME = lambda_.Runtime.PYTHON_3_12
LOG_RETENTION = logs.RetentionDays.THREE_DAYS


def python_function(
    scope: Construct,
    construct_id: str,
    *,
    code: lambda_.Code,
    handler: str,
    environment: Mapping[str, str],
    timeout: Duration,
    memory_size: int,
    dlq: sqs.IQueue,
    description: str,
    function_name: str,
) -> lambda_.Function:
    """Create a zip Lambda with bounded retries, a DLQ and short log retention."""
    log_group = logs.LogGroup(
        scope,
        f"{construct_id}LogGroup",
        log_group_name=f"/aws/lambda/{function_name}",
        retention=LOG_RETENTION,
        removal_policy=RemovalPolicy.RETAIN,
    )
    return lambda_.Function(
        scope,
        construct_id,
        code=code,
        function_name=function_name,
        log_group=log_group,
        handler=handler,
        runtime=PYTHON_RUNTIME,
        architecture=lambda_.Architecture.X86_64,
        environment=dict(environment),
        timeout=timeout,
        memory_size=memory_size,
        ephemeral_storage_size=Size.mebibytes(512),
        dead_letter_queue=dlq,
        dead_letter_queue_enabled=True,
        retry_attempts=0,
        description=description,
    )
