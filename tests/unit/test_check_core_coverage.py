"""Tests for ``tools/check_core_coverage.py``.

The script is the only thing that enforces the per-module threshold of
``AGENTS.md`` §8.3, so its arithmetic and its refusal to pass a module that was
never measured matter more than the happy path.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_core_coverage.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_core_coverage", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coverage_gate = _load_script()


def _file(
    covered_lines: int, num_statements: int, covered_branches: int = 0, num_branches: int = 0
) -> dict:
    return {
        "summary": {
            "covered_lines": covered_lines,
            "num_statements": num_statements,
            "covered_branches": covered_branches,
            "num_branches": num_branches,
        }
    }


def _write_report(tmp_path: Path, files: dict[str, dict]) -> Path:
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"files": files}), encoding="utf-8")
    return report


def test_percentages_are_computed_per_module() -> None:
    report = {
        "files": {
            "src/optimizer/core/problem.py": _file(90, 100),
            "src/optimizer/core/service.py": _file(10, 10),
            "src/executor/core/execution.py": _file(1, 10),
        }
    }

    percentages = coverage_gate.module_percentages(report)

    assert percentages["src/optimizer/core"] == (pytest.approx(100 * 100 / 110), 110)
    assert percentages["src/executor/core"] == (pytest.approx(10.0), 10)


def test_windows_separators_from_a_local_report_are_counted() -> None:
    report = {"files": {"src\\ingest\\core\\service.py": _file(60, 60)}}

    percentages = coverage_gate.module_percentages(report)

    assert percentages["src/ingest/core"] == (100.0, 60)


def test_branches_are_weighted_like_coverage_py() -> None:
    report = {
        "files": {
            "src/backtest/core/engine.py": _file(80, 80, covered_branches=10, num_branches=20)
        }
    }

    percentages = coverage_gate.module_percentages(report)

    assert percentages["src/backtest/core"] == (pytest.approx(100 * 90 / 100), 100)


def test_module_below_the_threshold_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _write_report(
        tmp_path,
        {
            "src/optimizer/core/problem.py": _file(99, 100),
            "src/executor/core/execution.py": _file(50, 100),
        },
    )

    exit_code = coverage_gate.main([str(report)])

    assert exit_code == 1
    assert "src/executor/core" in capsys.readouterr().err


def test_module_is_ok_when_all_measured_modules_clear_the_threshold(
    tmp_path: Path,
) -> None:
    files = {f"{module}/mod.py": _file(95, 100) for module in coverage_gate.MODULES}
    report = _write_report(tmp_path, files)

    assert coverage_gate.main([str(report)]) == 0


def test_a_module_missing_from_the_report_is_not_treated_as_covered(tmp_path: Path) -> None:
    report = _write_report(tmp_path, {"src/optimizer/core/problem.py": _file(100, 100)})

    exit_code = coverage_gate.main([str(report)])

    assert exit_code == 1


def test_missing_report_file_is_an_error_not_a_pass(tmp_path: Path) -> None:
    assert coverage_gate.main([str(tmp_path / "absent.json")]) == 2


def test_threshold_is_configurable(tmp_path: Path) -> None:
    report = _write_report(
        tmp_path, {f"{module}/mod.py": _file(85, 100) for module in coverage_gate.MODULES}
    )

    assert coverage_gate.main([str(report), "--threshold", "80"]) == 0
