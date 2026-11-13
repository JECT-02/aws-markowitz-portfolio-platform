from __future__ import annotations

import json
from typing import Any

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infra.stacks.data_stack import DataStack
from infra.stacks.pipeline_stack import PipelineStack
from tests.infra.helpers import deploy_config


def _template() -> Template:
    app = App()
    environment = Environment(account="123456789012", region="us-east-1")
    config = deploy_config()
    data = DataStack(app, "Data", config=config, env=environment)
    pipeline = PipelineStack(
        app,
        "Pipeline",
        config=config,
        data=data,
        env=environment,
    )
    return Template.from_stack(pipeline)


def test_functions_have_bounded_runtime_and_logs() -> None:
    template = _template()
    template.resource_count_is("AWS::Lambda::Function", 3)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "PackageType": "Image",
            "Timeout": 300,
            "MemorySize": 1024,
            "Architectures": ["x86_64"],
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "PackageType": "Image",
            "Timeout": 600,
            "MemorySize": 2048,
        },
    )
    repositories = template.find_resources("AWS::ECR::Repository")
    assert len(repositories) == 3
    template.resource_count_is("AWS::Logs::LogGroup", 3)
    template.has_resource_properties(
        "AWS::Logs::LogGroup",
        {"RetentionInDays": 3},
    )


def test_functions_receive_the_documented_environment() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "BRONZE_BUCKET": Match.any_value(),
                        "QUARANTINE_BUCKET": Match.any_value(),
                        "ALPACA_API_KEY_PARAM": "/portfolio-markowitz/test/alpaca/api-key",
                        "ALPACA_SECRET_KEY_PARAM": "/portfolio-markowitz/test/alpaca/secret-key",
                        "CONFIG_DIR": "/var/task/config",
                        "WINDOW_DAYS": "252",
                    }
                )
            }
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "PackageType": "Image",
            "Environment": {
                "Variables": Match.object_like(
                    {
                        "SILVER_BUCKET": Match.any_value(),
                        "GOLD_BUCKET": Match.any_value(),
                        "STATE_TABLE": Match.any_value(),
                    }
                )
            },
        },
    )


def test_functions_have_dead_letter_queues() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"DeadLetterConfig": {"TargetArn": Match.any_value()}},
    )


def test_ssm_policy_targets_only_the_two_parameters() -> None:
    template = _template()
    statements = [
        statement
        for resource in template.to_json()["Resources"].values()
        if resource["Type"] == "AWS::IAM::Policy"
        for statement in _statements(resource)
    ]
    ssm = [statement for statement in statements if statement.get("Action") == "ssm:GetParameter"]
    assert len(ssm) == 1
    resources = _flatten(ssm[0]["Resource"])
    assert "/portfolio-markowitz/test/alpaca/api-key" in resources
    assert "/portfolio-markowitz/test/alpaca/secret-key" in resources


def test_state_machine_has_explicit_failure_path() -> None:
    template = _template()
    resources = template.to_json()["Resources"]
    machine = next(
        resource
        for resource in resources.values()
        if resource["Type"] == "AWS::StepFunctions::StateMachine"
    )
    definition = json.loads(_flatten(machine["Properties"]["DefinitionString"]))
    assert definition["StartAt"] == "Ingest"
    states = definition["States"]
    assert set(states) == {
        "Ingest",
        "CheckMarketCalendar",
        "MarketClosed",
        "Clean",
        "Optimize",
        "NotifySuccess",
        "NotifyFailure",
        "PipelineFailed",
        "PipelineSucceeded",
    }
    assert states["Ingest"]["Catch"][0]["Next"] == "NotifyFailure"
    assert states["Optimize"]["Catch"][0]["Next"] == "NotifyFailure"
    assert states["CheckMarketCalendar"]["Choices"][0]["StringEquals"] == "SKIPPED"
    clean_parameters = states["Clean"]["Parameters"]
    assert clean_parameters["accepted.$"] == "$.accepted"


def test_scheduler_uses_new_york_close() -> None:
    template = _template()
    template.has_resource_properties(
        "AWS::Scheduler::Schedule",
        {
            "ScheduleExpression": "cron(30 16 ? * MON-FRI *)",
            "ScheduleExpressionTimezone": "America/New_York",
            "State": "ENABLED",
            "FlexibleTimeWindow": {"Mode": "OFF"},
        },
    )


def test_no_iam_statement_uses_wildcards() -> None:
    template = _template()
    for resource in template.to_json()["Resources"].values():
        if resource["Type"] not in {"AWS::IAM::Policy", "AWS::IAM::Role"}:
            continue
        for statement in _statements(resource):
            assert statement.get("Action") != "*"
            assert statement.get("Resource") != "*"


def test_every_resource_is_tagged() -> None:
    template = _template()
    for resource in template.to_json()["Resources"].values():
        properties = resource.get("Properties", {})
        tags = properties.get("Tags")
        if tags is None:
            continue
        keys = {tag.get("Key") for tag in tags}
        assert {"project", "env", "owner", "cost-center"} <= keys


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_flatten(item) for item in value)
    if isinstance(value, dict) and "Fn::Join" in value:
        delimiter, parts = value["Fn::Join"]
        return str(delimiter).join(_flatten(part) for part in parts)
    return "TOKEN"


def _statements(resource: dict[str, Any]) -> list[dict[str, Any]]:
    properties = resource.get("Properties", {})
    document = properties.get("PolicyDocument") or properties.get("AssumeRolePolicyDocument")
    if not isinstance(document, dict):
        return []
    statements = document.get("Statement", [])
    if not isinstance(statements, list):
        return []
    return [statement for statement in statements if isinstance(statement, dict)]
