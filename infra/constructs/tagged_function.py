from __future__ import annotations

from collections.abc import Mapping

from aws_cdk import Duration, Size
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
) -> lambda_.Function:
    """Create a zip Lambda with bounded retries, a DLQ and short log retention."""
    return lambda_.Function(
        scope,
        construct_id,
        code=code,
        handler=handler,
        runtime=PYTHON_RUNTIME,
        architecture=lambda_.Architecture.X86_64,
        environment=dict(environment),
        timeout=timeout,
        memory_size=memory_size,
        ephemeral_storage_size=Size.megabytes(512),
        dead_letter_queue=dlq,
        dead_letter_queue_enabled=True,
        retry_attempts=0,
        log_retention=LOG_RETENTION,
        description=description,
    )
