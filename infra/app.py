from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk

from infra.config import PROJECT_NAME, load_deploy_config
from infra.stacks.data_stack import DataStack

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app = cdk.App()
    config = load_deploy_config(PROJECT_ROOT / "config")
    environment = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION"),
    )
    DataStack(
        app,
        f"{PROJECT_NAME}-data-{config.env_name}",
        config=config,
        env=environment,
    )
    app.synth()


if __name__ == "__main__":
    main()
