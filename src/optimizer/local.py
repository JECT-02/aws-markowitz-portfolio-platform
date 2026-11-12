from __future__ import annotations

import json
from pathlib import Path

from common.env import load_env_file
from common.logging import configure_logging
from optimizer.handler import build_dependencies, run_optimizer


def main() -> int:
    """Run one local optimization against the configured silver lakehouse."""
    load_env_file(Path(".env"))
    configure_logging()
    result = run_optimizer({}, build_dependencies())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
