from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common.errors import StorageError
from executor.core.models import ExecutionResult


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
        message = {
            "proposal_id": result.proposal_id,
            "status": result.status.value,
        }
        if result.error is not None:
            message["error"] = result.error
        try:
            self._sns.publish(
                TopicArn=self._topic_arn,
                Subject=f"portfolio rebalance {result.status.value}",
                Message=json.dumps(message),
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("sns notification publish failed") from exc
