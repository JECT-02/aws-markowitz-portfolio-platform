from __future__ import annotations

import json

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from infra.config import PROJECT_NAME, DeployConfig
from infra.constructs.container_function import container_function
from infra.constructs.tagged import apply_tags
from infra.stacks.data_stack import DataStack

SCHEDULE_EXPRESSION = "cron(30 16 ? * MON-FRI *)"
SCHEDULE_TIMEZONE = "America/New_York"
TRANSIENT_LAMBDA_ERRORS = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
]


class PipelineStack(Stack):
    """Daily slow loop: ingest, clean, optimize and notify."""

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

        ingest_repository = self._repository("ingest")
        clean_repository = self._repository("clean")
        optimizer_repository = self._repository("optimizer")

        runtime_environment = {
            "CODE_VERSION": config.code_version,
            "CONFIG_DIR": "/var/task/config",
        }
        ingest = container_function(
            self,
            "IngestFunction",
            function_name=f"{PROJECT_NAME}-ingest-{config.env_name}",
            repository=ingest_repository,
            image_tag=config.image_tag,
            command=["ingest.handler.handler"],
            environment={
                **runtime_environment,
                "BRONZE_BUCKET": data.bronze_bucket.bucket_name,
                "QUARANTINE_BUCKET": data.quarantine_bucket.bucket_name,
                "ALPACA_API_KEY_PARAM": config.api_key_parameter,
                "ALPACA_SECRET_KEY_PARAM": config.secret_key_parameter,
                **config.strategy_environment,
            },
            timeout=Duration.minutes(5),
            memory_size=1024,
            dlq=data.ingest_dlq,
            description="Daily market data ingestion",
        )
        ingest.add_to_role_policy(self._parameter_policy(config))
        clean = container_function(
            self,
            "CleanFunction",
            function_name=f"{PROJECT_NAME}-clean-{config.env_name}",
            repository=clean_repository,
            image_tag=config.image_tag,
            command=["clean.handler.handler"],
            environment={
                **runtime_environment,
                "BRONZE_BUCKET": data.bronze_bucket.bucket_name,
                "SILVER_BUCKET": data.silver_bucket.bucket_name,
                **config.strategy_environment,
            },
            timeout=Duration.minutes(5),
            memory_size=1024,
            dlq=data.clean_dlq,
            description="Silver returns cleaning",
        )
        optimizer = container_function(
            self,
            "OptimizerFunction",
            function_name=f"{PROJECT_NAME}-optimizer-{config.env_name}",
            repository=optimizer_repository,
            image_tag=config.image_tag,
            command=["optimizer.handler.handler"],
            environment={
                **runtime_environment,
                "SILVER_BUCKET": data.silver_bucket.bucket_name,
                "GOLD_BUCKET": data.gold_bucket.bucket_name,
                "STATE_TABLE": data.state_table.table_name,
                **config.strategy_environment,
            },
            timeout=Duration.minutes(10),
            memory_size=2048,
            dlq=data.optimizer_dlq,
            description="Markowitz optimizer",
        )

        data.bronze_bucket.grant_read(ingest)
        data.bronze_bucket.grant_put(ingest)
        data.quarantine_bucket.grant_put(ingest)
        data.bronze_bucket.grant_read(clean)
        data.silver_bucket.grant_read_write(clean)
        data.silver_bucket.grant_read(optimizer)
        data.gold_bucket.grant_read_write(optimizer)
        data.state_table.grant_read_write_data(optimizer)

        state_machine = self._state_machine(data, ingest, clean, optimizer)
        data.notifications.grant_publish(state_machine)
        self._schedule(data, state_machine)

        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "IngestRepositoryUri", value=ingest_repository.repository_uri)
        CfnOutput(self, "CleanRepositoryUri", value=clean_repository.repository_uri)
        CfnOutput(self, "OptimizerRepositoryUri", value=optimizer_repository.repository_uri)

    def _repository(self, name: str) -> ecr.Repository:
        return ecr.Repository(
            self,
            f"{name.capitalize()}Repository",
            repository_name=f"{PROJECT_NAME}-{name}-{self._config.env_name}",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    rule_priority=1,
                    description=f"Expire untagged {name} images",
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=Duration.days(14),
                )
            ],
        )

    def _parameter_policy(self, config: DeployConfig) -> iam.PolicyStatement:
        return iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                self.format_arn(
                    service="ssm",
                    resource=f"parameter{config.api_key_parameter}",
                ),
                self.format_arn(
                    service="ssm",
                    resource=f"parameter{config.secret_key_parameter}",
                ),
            ],
        )

    def _state_machine(
        self,
        data: DataStack,
        ingest: lambda_.Function,
        clean: lambda_.Function,
        optimizer: lambda_.Function,
    ) -> sfn.StateMachine:
        failure = sfn.Fail(
            self,
            "PipelineFailed",
            error="PipelineFailure",
            cause="A pipeline stage failed",
        )
        notify_failure = tasks.SnsPublish(
            self,
            "NotifyFailure",
            topic=data.notifications,
            message=sfn.TaskInput.from_json_path_at("$"),
            subject="Portfolio pipeline failure",
        ).next(failure)
        success = sfn.Succeed(self, "PipelineSucceeded")

        ingest_task = tasks.LambdaInvoke(
            self,
            "Ingest",
            lambda_function=ingest,
            payload=sfn.TaskInput.from_object({"source": "alpaca"}),
            payload_response_only=True,
            retry_on_service_exceptions=False,
            state_name="Ingest",
        )
        ingest_task.add_retry(
            errors=TRANSIENT_LAMBDA_ERRORS,
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )
        ingest_task.add_catch(notify_failure, result_path="$.error")

        clean_task = tasks.LambdaInvoke(
            self,
            "Clean",
            lambda_function=clean,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id.$": "$.run_id",
                    "run_date.$": "$.run_date",
                    "source.$": "$.source",
                    "accepted.$": "$.accepted",
                }
            ),
            payload_response_only=True,
            retry_on_service_exceptions=False,
            state_name="Clean",
        )
        clean_task.add_retry(
            errors=TRANSIENT_LAMBDA_ERRORS,
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )
        clean_task.add_catch(notify_failure, result_path="$.error")

        optimizer_task = tasks.LambdaInvoke(
            self,
            "Optimize",
            lambda_function=optimizer,
            payload=sfn.TaskInput.from_object(
                {
                    "run_id.$": "$.run_id",
                    "run_date.$": "$.run_date",
                }
            ),
            payload_response_only=True,
            retry_on_service_exceptions=False,
            state_name="Optimize",
        )
        optimizer_task.add_retry(
            errors=TRANSIENT_LAMBDA_ERRORS,
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0,
        )
        optimizer_task.add_catch(notify_failure, result_path="$.error")

        notify_success = tasks.SnsPublish(
            self,
            "NotifySuccess",
            topic=data.notifications,
            message=sfn.TaskInput.from_json_path_at("$"),
            subject="Portfolio proposal ready",
        ).next(success)

        market_closed = sfn.Succeed(self, "MarketClosed")
        choice = sfn.Choice(self, "CheckMarketCalendar").when(
            sfn.Condition.string_equals("$.status", "SKIPPED"),
            market_closed,
        )
        choice.otherwise(clean_task.next(optimizer_task).next(notify_success))

        return sfn.StateMachine(
            self,
            "DailyPipeline",
            state_machine_name=f"{PROJECT_NAME}-daily-{self._config.env_name}",
            definition_body=sfn.DefinitionBody.from_chainable(ingest_task.next(choice)),
            timeout=Duration.minutes(15),
        )

    def _schedule(self, data: DataStack, state_machine: sfn.StateMachine) -> None:
        role = iam.Role(
            self,
            "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
            inline_policies={
                "StartExecution": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["states:StartExecution"],
                            resources=[state_machine.state_machine_arn],
                        )
                    ]
                ),
                "ScheduleDeadLetter": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["sqs:SendMessage"],
                            resources=[data.schedule_dlq.queue_arn],
                        )
                    ]
                ),
            },
        )
        scheduler.CfnSchedule(
            self,
            "DailySchedule",
            name=f"{PROJECT_NAME}-daily-{self._config.env_name}",
            description="Daily optimization at the New York close",
            state="ENABLED",
            schedule_expression=SCHEDULE_EXPRESSION,
            schedule_expression_timezone=SCHEDULE_TIMEZONE,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=state_machine.state_machine_arn,
                role_arn=role.role_arn,
                input=json.dumps({"source": "alpaca"}),
                dead_letter_config=scheduler.CfnSchedule.DeadLetterConfigProperty(
                    arn=data.schedule_dlq.queue_arn
                ),
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(
                    maximum_retry_attempts=3,
                    maximum_event_age_in_seconds=3600,
                ),
            ),
        )
