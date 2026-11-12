from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from common.errors import ConfigurationError


def load_env_file(path: Path, environ: dict[str, str] | None = None) -> None:
    """Load simple KEY=VALUE pairs without overriding existing variables."""
    target = os.environ if environ is None else environ
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        key = name.strip()
        if not key or key in target:
            continue
        target[key] = value.strip().strip('"').strip("'")


def require_variable(environ: Mapping[str, str], name: str) -> str:
    """Return a required environment variable or raise a configuration error."""
    value = environ.get(name)
    if value is None or not value.strip():
        raise ConfigurationError(f"missing environment variable: {name}")
    return value.strip()
