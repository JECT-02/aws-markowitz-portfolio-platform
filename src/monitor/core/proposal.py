"""Gold layer access for the fast monitoring loop.

The monitor reads only the latest proposal document; it never writes to the
gold bucket. The repository lives here instead of importing ``reader`` to
keep the fast loop independent from the read API (AGENTS.md section 3).
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from common.errors import DataValidationError, NotFoundError, StorageError
from common.storage import ObjectStorage
from optimizer.core.models import Proposal

LATEST_KEY = "latest.json"


class GoldProposalReader(Protocol):
    """Loads the latest proposal document from the gold layer."""

    def read_latest(self) -> Proposal: ...


class S3LatestProposal:
    """Gold layer adapter backed by S3 through the shared object storage."""

    def __init__(self, storage: ObjectStorage, bucket: str) -> None:
        self._storage = storage
        self._bucket = bucket

    def read_latest(self) -> Proposal:
        """Return the latest proposal; raise ``NotFoundError`` when absent.

        Preconditions: the gold bucket holds optimizer artifacts under the
        documented layout. Postconditions: the returned proposal passed full
        Pydantic validation; malformed stored data raises
        ``DataValidationError`` and never reaches the drift computation.
        """
        if not self._storage.exists(self._bucket, LATEST_KEY):
            raise NotFoundError(f"proposal not found for {LATEST_KEY}")
        try:
            document: dict[str, Any] = self._storage.read_json(self._bucket, LATEST_KEY)
        except StorageError as exc:
            raise StorageError(f"failed to read proposal {LATEST_KEY}") from exc
        try:
            return Proposal.model_validate(document)
        except ValidationError as exc:
            raise DataValidationError(f"stored proposal is invalid: {LATEST_KEY}") from exc
