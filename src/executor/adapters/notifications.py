from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import StorageError
from executor.core.models import ExecutionResult, ProposalStatus


class SnsResultNotifier:
    """Publishes the final rebalance outcome to the notifications topic.

    Preconditions: the topic exists and the caller's role can publish.
    Postconditions: the message carries only proposal id, status and error;
    account data is never included (AGENTS.md section 6.14).
    """

    def __init__(self, topic_arn: str, region_name: str, sns_client: Any | None = None) -> None:
        self._topic_arn = topic_arn
        self._sns: Any = (
            sns_client
            if sns_client is not None
            else boto3.client(
                "sns",
                region_name=region_name,
            )
        )

    def notify(self, result: ExecutionResult) -> None:
        """Publish one final result; raises StorageError on sns failures."""
        message: dict[str, str] = {
            "proposal_id": result.proposal_id,
            "status": result.status.value,
        }
        if result.error is not None:
            message["error"] = result.error
        self._publish(result.status.value, message)

    def notify_failure(self, proposal_id: str, status: ProposalStatus, reason: str) -> None:
        """Publish a failed or rejected attempt, carrying the proposal's real status.

        A rejection before execution keeps the proposal PENDING, so the subject
        says "rejected" instead of implying the proposal itself failed.
        """
        message = {
            "proposal_id": proposal_id,
            "status": status.value,
            "error": reason,
        }
        label = "attempt rejected" if status is ProposalStatus.PENDING else status.value
        self._publish(label, message)

    def _publish(self, label: str, message: dict[str, str]) -> None:
        try:
            self._sns.publish(
                TopicArn=self._topic_arn,
                Subject=f"portfolio rebalance {label}",
                Message=json.dumps(message),
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("sns notification publish failed") from exc
