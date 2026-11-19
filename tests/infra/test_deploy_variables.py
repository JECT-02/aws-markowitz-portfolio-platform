"""Static checks for the required deploy variables (mistakes.md QA-005/QA-027).

`terraform plan` is the only place where Terraform evaluates a variable's
default, so these checks pin the shape of the declarations that must stay
required: a value that cannot be guessed correctly would either fail the plan
with a confusing validation error (QA-005) or collide with another account in
the region (QA-027).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_MAIN = ROOT / "infra" / "envs" / "dev" / "main.tf"
API_MAIN = ROOT / "infra" / "modules" / "api" / "main.tf"


def _variable_block(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index('variable "' + name + '"')
    end = text.index(chr(10) + "}", start)
    return text[start:end]


def _has_default(block: str) -> bool:
    return re.search(r"^\s*default\s*=", block, re.MULTILINE) is not None


def test_image_digests_is_required_and_validated() -> None:
    block = _variable_block(ENV_MAIN, "image_digests")
    assert not _has_default(block), "a default digest map cannot point at a real image"
    assert "validation" in block
    assert "TF_VAR_image_digests" in block


def test_cognito_domain_prefix_is_required_in_the_environment() -> None:
    block = _variable_block(ENV_MAIN, "cognito_domain_prefix")
    assert not _has_default(block), "the prefix is unique per region; a default can be taken"
    assert "TF_VAR_cognito_domain_prefix" in block
    assert (
        ENV_MAIN.read_text(encoding="utf-8").count(
            "cognito_domain_prefix = var.cognito_domain_prefix"
        )
        == 1
    )


def test_the_api_module_validates_the_cognito_prefix() -> None:
    block = _variable_block(API_MAIN, "cognito_domain_prefix")
    assert not _has_default(block)
    assert "validation" in block


def test_the_example_and_the_readme_name_both_variables() -> None:
    example = (ROOT / "infra" / "envs" / "dev" / "terraform.tfvars.example").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for variable in ("image_digests", "cognito_domain_prefix"):
        assert variable in example, f"{variable} missing from terraform.tfvars.example"
        assert f"TF_VAR_{variable}" in readme, f"{variable} missing from README"
