from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk

from infra.config import PROJECT_NAME, load_deploy_config
from infra.stacks.api_stack import ApiStack
from infra.stacks.data_stack import DataStack
from infra.stacks.pipeline_stack import PipelineStack
from infra.stacks.web_stack import WebStack

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app = cdk.App()
    config = load_deploy_config(PROJECT_ROOT / "config")
    environment = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT") or None,
        region=os.environ.get("CDK_DEFAULT_REGION") or None,
    )
    data = DataStack(
        app,
        f"{PROJECT_NAME}-data-{config.env_name}",
        config=config,
        env=environment,
    )
    PipelineStack(
        app,
        f"{PROJECT_NAME}-pipeline-{config.env_name}",
        config=config,
        data=data,
        env=environment,
    )
    api = ApiStack(
        app,
        f"{PROJECT_NAME}-api-{config.env_name}",
        config=config,
        data=data,
        env=environment,
    )
    WebStack(
        app,
        f"{PROJECT_NAME}-web-{config.env_name}",
        config=config,
        api=api,
        env=environment,
    )
    app.synth()


if __name__ == "__main__":
    main()
