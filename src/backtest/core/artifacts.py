from __future__ import annotations

from backtest.core.models import BacktestReport
from common.storage import ObjectStorage

BACKTEST_KEY_TEMPLATE = "run_date={run_date}/backtest.json"
BACKTEST_LATEST_KEY = "backtest_latest.json"


class S3BacktestWriter:
    """Persists the weekly backtest report to the gold layer."""

    def __init__(self, storage: ObjectStorage, gold_bucket: str) -> None:
        self._storage = storage
        self._gold_bucket = gold_bucket

    def write(self, report: BacktestReport) -> None:
        """Write the report; reruns for the same run date are idempotent.

        Preconditions: ``report`` is a validated ``BacktestReport``.
        Postconditions: ``run_date=<date>/backtest.json`` holds the first
        payload written for the run date (concurrent reruns never overwrite
        it); ``backtest_latest.json`` always holds the most recent report.
        """
        payload = report.model_dump(mode="json")
        self._storage.write_json_if_absent(
            self._gold_bucket,
            BACKTEST_KEY_TEMPLATE.format(run_date=report.metadata.run_date.isoformat()),
            payload,
        )
        self._storage.write_json(self._gold_bucket, BACKTEST_LATEST_KEY, payload)
