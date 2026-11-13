from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = [
    ROOT / "src" / "ingest" / "requirements.txt",
    ROOT / "src" / "clean" / "requirements.txt",
    ROOT / "src" / "optimizer" / "requirements.txt",
]
DOCKERFILES = {
    name: ROOT / "src" / name / "Dockerfile" for name in ("ingest", "clean", "optimizer")
}


def _locked_versions() -> dict[str, str]:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {
        package["name"].lower().replace("_", "-"): package["version"] for package in data["package"]
    }


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, version = line.split("==", 1)
        pins[name.lower().replace("_", "-")] = version
    return pins


@pytest.mark.parametrize("path", REQUIREMENTS)
def test_requirements_are_exactly_pinned(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            assert "==" in line


@pytest.mark.parametrize("path", REQUIREMENTS)
def test_requirements_match_the_lockfile(path: Path) -> None:
    locked = _locked_versions()
    for name, version in _pins(path).items():
        assert locked.get(name) == version, f"{name} is not pinned to uv.lock"


@pytest.mark.parametrize("path", REQUIREMENTS)
def test_boto3_is_not_bundled(path: Path) -> None:
    assert "boto3" not in _pins(path)


@pytest.mark.parametrize("name", DOCKERFILES)
def test_containers_use_the_pinned_requirements(name: str) -> None:
    dockerfile = DOCKERFILES[name].read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM public.ecr.aws/lambda/python:3.12")
    assert f"COPY src/{name}/requirements.txt" in dockerfile
    assert f'CMD ["{name}.handler.handler"]' in dockerfile
    assert "USER appuser" in dockerfile
