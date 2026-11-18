from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULES = ("ingest", "clean", "optimizer", "reader", "executor", "monitor", "backtest")
DOCKERFILES = {name: ROOT / "src" / name / "Dockerfile" for name in MODULES}
LOCK_REQUIREMENTS = ROOT / "build" / "requirements.lock.txt"


def _project_dependencies() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [item.split(">=")[0].split("==")[0].strip() for item in data["project"]["dependencies"]]


def _pinned_names(requirements: Path) -> set[str]:
    return {
        line.split("==", 1)[0].strip().lower().replace("_", "-")
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and "==" in line
    }


@pytest.fixture
def frozen_requirements(tmp_path: Path) -> Path:
    """Generate the same file `make freeze` produces, from the real lock."""
    output = tmp_path / "requirements.lock.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-hashes",
            "--no-dev",
            "--no-emit-project",
            "-o",
            str(output),
        ],
        check=True,
        cwd=ROOT,
        capture_output=True,
    )
    return output


@pytest.mark.parametrize("name", MODULES)
def test_containers_install_the_frozen_requirements(name: str) -> None:
    dockerfile = DOCKERFILES[name].read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM public.ecr.aws/lambda/python:3.12")
    assert "COPY build/requirements.lock.txt ./requirements.txt" in dockerfile
    assert f'CMD ["{name}.handler.handler"]' in dockerfile
    assert "USER appuser" in dockerfile


def test_frozen_requirements_cover_every_runtime_dependency(
    frozen_requirements: Path,
) -> None:
    """L-04: direct and transitive dependencies are pinned from uv.lock."""
    pinned = _pinned_names(frozen_requirements)
    for dependency in _project_dependencies():
        assert dependency.lower().replace("_", "-") in pinned, (
            f"{dependency} is missing from the frozen requirements"
        )


def test_frozen_requirements_use_exact_versions(frozen_requirements: Path) -> None:
    for line in frozen_requirements.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert "==" in stripped, f"unpinned line: {stripped}"


def test_make_freeze_target_generates_the_expected_file() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert (
        "uv export --frozen --no-hashes --no-dev --no-emit-project -o build/requirements.lock.txt"
    ) in makefile
