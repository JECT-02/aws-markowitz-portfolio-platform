from __future__ import annotations

from aws_cdk import Tags
from constructs import Construct

from infra.config import PROJECT_NAME, DeployConfig


def apply_tags(scope: Construct, config: DeployConfig) -> None:
    """Attach the mandatory cost and ownership tags to a scope."""
    Tags.of(scope).add("project", PROJECT_NAME)
    Tags.of(scope).add("env", config.env_name)
    Tags.of(scope).add("owner", config.owner)
    Tags.of(scope).add("cost-center", config.cost_center)
