from __future__ import annotations

from datetime import date

from pydantic import ValidationError

from common.errors import DataValidationError
from optimizer.core.models import Proposal
from reader.core.models import BacktestReport, MonitorSnapshot
from reader.core.repository import GoldRepository, MonitorHistory, StateReader


class ReaderService:
    """Shapes the gold layer artifacts and portfolio state into read models.

    Precondition: the injected repositories return raw gold documents and
    DynamoDB attribute maps. Postcondition: every returned object passed
    Pydantic validation; missing artifacts raise ``NotFoundError`` and
    malformed stored data raises ``DataValidationError``.
    """

    def __init__(
        self,
        gold: GoldRepository,
        state: StateReader | None = None,
        monitor_history: MonitorHistory | None = None,
    ) -> None:
        self._gold = gold
        self._state = state
        self._monitor_history = monitor_history

    def get_proposal(self, run_date: date | None = None) -> Proposal:
        document = self._gold.load_proposal(run_date)
        try:
            return Proposal.model_validate(document)
        except ValidationError as exc:
            raise DataValidationError(
                f"stored proposal is invalid: {_document_key(run_date)}"
            ) from exc

    def get_metrics(self, run_date: date | None = None) -> dict[str, object]:
        """Return the metrics document with its proposal attribution (N-07).

        BI consumers need the run date and the proposal id to build time
        series, so the response carries them next to the metrics map.
        """
        proposal = self.get_proposal(run_date)
        return {
            "proposal_id": proposal.proposal_id,
            "run_date": proposal.run_date.isoformat(),
            "metrics": proposal.metrics,
        }

    def get_monitor(self) -> MonitorSnapshot:
        proposal = self.get_proposal(None)
        current = self._state.read_current_state() if self._state is not None else None
        snapshot = MonitorSnapshot(
            proposal_id=proposal.proposal_id,
            run_date=proposal.run_date,
            drift_l1=proposal.drift_l1,
            rebalance_required=proposal.rebalance_required,
            weights=current.weights if current is not None else proposal.actual_weights,
            reconciled_at=current.reconciled_at if current is not None else None,
        )
        if self._monitor_history is not None:
            record = self._monitor_history.read_latest_monitor()
            if record is not None and record.proposal_id == proposal.proposal_id:
                snapshot = MonitorSnapshot(
                    proposal_id=snapshot.proposal_id,
                    run_date=snapshot.run_date,
                    drift_l1=snapshot.drift_l1,
                    rebalance_required=snapshot.rebalance_required,
                    weights=snapshot.weights,
                    reconciled_at=snapshot.reconciled_at,
                    live_weights=record.weights_live,
                    live_drift_l1=record.drift_l1,
                    monitor_updated_at=record.computed_at,
                )
        return snapshot

    def get_backtest(self) -> BacktestReport:
        """Return the latest weekly backtest report."""
        document = self._gold.load_backtest()
        try:
            return BacktestReport.model_validate(document)
        except ValidationError as exc:
            raise DataValidationError("stored backtest report is invalid") from exc


def _document_key(run_date: date | None) -> str:
    """Return the gold key used for a query, for error messages only."""
    if run_date is None:
        return "latest.json"
    return f"run_date={run_date.isoformat()}/proposal.json"
