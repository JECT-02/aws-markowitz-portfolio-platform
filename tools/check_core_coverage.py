"""Fail when a critical ``core/`` package falls under its coverage threshold.

``AGENTS.md`` §8.3 sets the threshold per module (optimizer, executor and
ingest), but ``coverage.py`` only supports one global ``fail_under``. A single
combined number lets a small package regress while the larger ones keep the
average above the line, so the threshold is checked here, one module at a time,
against the JSON report of a single test run.

Usage::

    python scripts/check_core_coverage.py build/coverage-core.json [--threshold 90]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Module path relative to the repository root, as spelled in the Makefile.
MODULES: tuple[str, ...] = (
    "src/optimizer/core",
    "src/executor/core",
    "src/ingest/core",
    "src/backtest/core",
)

DEFAULT_THRESHOLD = 90.0


def _normalize(path: str) -> str:
    """Coverage reports paths with the platform separator; compare with ``/``."""
    return path.replace("\\", "/")


def _module_of(path: str) -> str | None:
    """Return the listed module a reported file belongs to, if any."""
    normalized = _normalize(path)
    for module in MODULES:
        if normalized.startswith(f"{module}/"):
            return module
    return None


def module_percentages(report: dict[str, Any]) -> dict[str, tuple[float, int]]:
    """Map each module to ``(percent_covered, measured_units)``.

    Percentages weight statements and branches by how many of each exist, which
    is how ``coverage.py`` computes its own total. A module with no reported
    file is returned with a count of zero so the caller can refuse it instead of
    reading 0/0 as 100%.
    """
    totals: dict[str, list[int]] = {module: [0, 0] for module in MODULES}
    for path, data in report.get("files", {}).items():
        module = _module_of(path)
        if module is None:
            continue
        summary = data["summary"]
        totals[module][0] += summary["covered_lines"] + summary["covered_branches"]
        totals[module][1] += summary["num_statements"] + summary["num_branches"]

    percentages: dict[str, tuple[float, int]] = {}
    for module, (covered, total) in totals.items():
        percent = 100.0 * covered / total if total else 100.0
        percentages[module] = (percent, total)
    return percentages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="JSON report written by --cov-report=json")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)

    if not args.report.is_file():
        print(f"error: no coverage report at {args.report}", file=sys.stderr)
        return 2
    report = json.loads(args.report.read_text(encoding="utf-8"))

    failures: list[str] = []
    for module, (percent, units) in module_percentages(report).items():
        if units == 0:
            # Without this the module would pass at 100% because it was never measured.
            failures.append(f"{module}: no fue medido (¿falta en --cov?)")
            print(f"{module:<22} sin datos")
            continue
        status = "ok" if percent >= args.threshold else "BAJO"
        print(f"{module:<22} {percent:6.2f}%  ({units} unidades)  {status}")
        if percent < args.threshold:
            failures.append(f"{module}: {percent:.2f}% < {args.threshold:g}%")

    if failures:
        print("\nUmbral de cobertura no alcanzado:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
