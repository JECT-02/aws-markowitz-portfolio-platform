from __future__ import annotations

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from common.errors import StorageError
from executor.adapters.notifications import SnsResultNotifier
from executor.core.models import (
    ExecutionResult,
    FilledOrder,
    ProposalStatus,
    Reconciliation,
)

TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:notifications"


def _result(status: ProposalStatus = ProposalStatus.EXECUTED) -> ExecutionResult:
    return ExecutionResult(
        proposal_id="2026-11-13-run-abc",
        status=status,
        orders=[
            FilledOrder(
                client_order_id="prop-SPY-sell",
                symbol="SPY",
                side="sell",
                requested_quantity=1.0,
                filled_quantity=1.0,
                status="filled",
            ),
        ],
        reconciliation=Reconciliation(
            weights={"SPY": 1.0},
            account_value=1_000.0,
            reconciled_at="2026-11-13T21:00:00Z",
        ),
    )


class _RecordingSns:
    def __init__(self) -> None:
        self.published: list[dict[str, str]] = []

    def publish(self, **kwargs: str) -> None:
        self.published.append(kwargs)


class _FailingSns:
    def publish(self, **kwargs: str) -> None:
        raise ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "Publish",
        )


def test_publishes_only_the_safe_summary_fields() -> None:
    sns = _RecordingSns()
    notifier = SnsResultNotifier(topic_arn=TOPIC_ARN, region_name="us-east-1", sns_client=sns)

    notifier.notify(_result(ProposalStatus.PARTIAL))

    assert len(sns.published) == 1
    message = json.loads(sns.published[0]["Message"])
    assert message == {"proposal_id": "2026-11-13-run-abc", "status": "PARTIAL"}
    assert sns.published[0]["TopicArn"] == TOPIC_ARN
    assert sns.published[0]["Subject"] == "portfolio rebalance PARTIAL"


def test_failed_results_include_the_error() -> None:
    sns = _RecordingSns()
    notifier = SnsResultNotifier(topic_arn=TOPIC_ARN, region_name="us-east-1", sns_client=sns)

    result = ExecutionResult(
        proposal_id="p1",
        status=ProposalStatus.FAILED,
        orders=[],
        error="no order filled",
    )
    notifier.notify(result)

    message = json.loads(sns.published[0]["Message"])
    assert message["status"] == "FAILED"
    assert message["error"] == "no order filled"


def test_publish_failure_raises_storage_error() -> None:
    notifier = SnsResultNotifier(
        topic_arn=TOPIC_ARN,
        region_name="us-east-1",
        sns_client=_FailingSns(),
    )
    with pytest.raises(StorageError):
        notifier.notify(_result())


@mock_aws
def test_publishes_against_a_moto_topic() -> None:
    sns = boto3.client("sns", region_name="us-east-1")
    topic = sns.create_topic(Name="notifications")
    notifier = SnsResultNotifier(
        topic_arn=topic["TopicArn"],
        region_name="us-east-1",
        sns_client=sns,
    )
    notifier.notify(_result())
