from __future__ import annotations

from common.errors import ConfigurationError
from common.logging import get_logger
from common.models import DataSource

logger = get_logger(__name__)


def resolve_report_source(sources: frozenset[str]) -> DataSource:
    """Attribute an artifact to the source(s) recorded in the silver data.

    Preconditions: ``sources`` are the ``source`` values from the silver
    partition metadata actually consumed. Postconditions: a single known
    source maps to itself, a mixture maps to ``DataSource.MIXED`` and is
    logged (AGENTS.md section 7.3), an empty or unknown set fails closed.
    """
    if len(sources) == 1:
        name = next(iter(sources))
        try:
            return DataSource(name)
        except ValueError as exc:
            raise ConfigurationError(
                f"silver partitions carry an unsupported source: {name}"
            ) from exc
    if len(sources) > 1:
        logger.warning(
            "artifact consumed mixed-source silver data",
            extra={"sources": sorted(sources)},
        )
        return DataSource.MIXED
    raise ConfigurationError("silver partitions carry no source metadata")
