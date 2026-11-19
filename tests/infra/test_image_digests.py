"""Static checks on the container image wiring of the Terraform modules.

`repository_url` is only known at apply time, so `terraform test` cannot read
the final `image_uri` at plan time. These checks pin the two defects reported in
mistakes.md: a digest is addressed with `@` (QA-002) and every Lambda keeps the
digest published in its own ECR repository instead of sharing the optimizer's
(QA-003).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"

IMAGE_URI = re.compile(r"^\s*image_uri\s*=\s*(.+)$", re.MULTILINE)

MODULES = {
    "infra/modules/pipeline/main.tf": 'lookup(var.image_digests, each.key, "")',
    "infra/modules/api/main.tf": 'lookup(var.image_digests, "reader", "")',
    "infra/modules/api/executor.tf": 'lookup(var.image_digests, "executor", "")',
    "infra/modules/backtest/main.tf": 'lookup(var.image_digests, "backtest", "")',
}


def _image_uri_expressions(path: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return IMAGE_URI.findall(text)


def test_every_image_uri_addresses_a_digest_with_at_sign() -> None:
    for path in MODULES:
        for expression in _image_uri_expressions(path):
            assert "}@${" in expression, f"{path}: a digest needs '@', not ':' -> {expression}"
            assert "}:${" not in expression, f"{path}: tag-style image URI found -> {expression}"


def test_every_image_uri_reads_its_own_digest() -> None:
    for path, expected in MODULES.items():
        expressions = _image_uri_expressions(path)
        assert expressions, f"{path}: no image_uri found"
        for expression in expressions:
            assert expected in expression, f"{path}: expected {expected} in {expression}"


def test_the_environment_exports_one_digest_map_to_every_module() -> None:
    text = (INFRA / "envs" / "dev" / "main.tf").read_text(encoding="utf-8")
    assert text.count("image_digests = var.image_digests") == 3
    assert "optimizer_image_tag" not in text
    assert 'variable "image_digests"' in text
    assert "type        = map(string)" in text
