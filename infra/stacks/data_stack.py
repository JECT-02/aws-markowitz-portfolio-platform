from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from infra.config import DeployConfig
from infra.constructs.bucket_policy import secure_bucket
from infra.constructs.tagged import apply_tags


class DataStack(Stack):
    """Persistent storage, notifications and dead-letter queues."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: DeployConfig,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        apply_tags(self, config)

        self.bronze_bucket = secure_bucket(self, "BronzeBucket", versioned=True)
        self.quarantine_bucket = secure_bucket(
            self,
            "QuarantineBucket",
            versioned=False,
            expiration_days=90,
        )
        self.silver_bucket = secure_bucket(
            self,
            "SilverBucket",
            versioned=False,
            expiration_days=400,
        )
        self.gold_bucket = secure_bucket(
            self,
            "GoldBucket",
            versioned=False,
            expiration_days=400,
        )

        self.state_table = dynamodb.Table(
            self,
            "PortfolioState",
            partition_key=dynamodb.Attribute(
                name="pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="sk",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PROVISIONED,
            read_capacity=1,
            write_capacity=1,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery_specification=(
                dynamodb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=False,
                )
            ),
            time_to_live_attribute="ttl",
            deletion_protection=False,
        )

        self.notifications = sns.Topic(
            self,
            "Notifications",
            display_name="Portfolio Markowitz notifications",
            enforce_ssl=True,
        )
        if config.notification_email is not None:
            self.notifications.add_subscription(
                subscriptions.EmailSubscription(config.notification_email)
            )

        self.ingest_dlq = self._dead_letter_queue("IngestDlq")
        self.clean_dlq = self._dead_letter_queue("CleanDlq")
        self.optimizer_dlq = self._dead_letter_queue("OptimizerDlq")
        self.state_machine_dlq = self._dead_letter_queue("StateMachineDlq")
        self.schedule_dlq = self._dead_letter_queue("ScheduleDlq")

        CfnOutput(self, "BronzeBucketName", value=self.bronze_bucket.bucket_name)
        CfnOutput(
            self,
            "QuarantineBucketName",
            value=self.quarantine_bucket.bucket_name,
        )
        CfnOutput(self, "SilverBucketName", value=self.silver_bucket.bucket_name)
        CfnOutput(self, "GoldBucketName", value=self.gold_bucket.bucket_name)
        CfnOutput(self, "StateTableName", value=self.state_table.table_name)
        CfnOutput(self, "NotificationsTopicArn", value=self.notifications.topic_arn)

    def _dead_letter_queue(self, construct_id: str) -> sqs.Queue:
        return sqs.Queue(
            self,
            construct_id,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(4),
            removal_policy=RemovalPolicy.RETAIN,
        )
