from __future__ import annotations

from datetime import date
from typing import NoReturn

from pydantic import ValidationError

from common.errors import DataValidationError, NotFoundError
from optimizer.core.models import PortfolioMetrics, Proposal
from reader.core.models import MonitorSnapshot
from reader.core.repository import GoldRepository, StateReader


class ReaderService:
    """Shapes the gold layer artifacts and portfolio state into read models.

    Precondition: the injected repositories return raw gold documents and
    DynamoDB attribute maps. Postcondition: every returned object passed
    Pydantic validation; missing artifacts raise ``NotFoundError`` and
    malformed stored data raises ``DataValidationError``.
    """

    def __init__(self, gold: GoldRepository, state: StateReader | None = None) -> None:
        self._gold = gold
        self._state = state

    def get_proposal(self, run_date: date | None = None) -> Proposal:
        document = self._gold.load_proposal(run_date)
        try:
            return Proposal.model_validate(document)
        except ValidationError as exc:
            raise DataValidationError(
                f"stored proposal is invalid: {_document_key(run_date)}"
            ) from exc

    def get_metrics(self, run_date: date | None = None) -> dict[str, PortfolioMetrics]:
        return self.get_proposal(run_date).metrics

    def get_monitor(self) -> MonitorSnapshot:
        proposal = self.get_proposal(None)
        current = self._state.read_current_state() if self._state is not None else None
        return MonitorSnapshot(
            proposal_id=proposal.proposal_id,
            run_date=proposal.run_date,
            drift_l1=proposal.drift_l1,
            rebalance_required=proposal.rebalance_required,
            weights=current.weights if current is not None else proposal.actual_weights,
            reconciled_at=current.reconciled_at if current is not None else None,
        )

    def get_backtest(self) -> NoReturn:
        """Backtests are produced by the weekly loop in phase 6; nothing exists yet."""
        raise NotFoundError("backtest results are not available until phase 6")


def _document_key(run_date: date | None) -> str:
    """Return the gold key used for a query, for error messages only."""
    if run_date is None:
        return "latest.json"
    return f"run_date={run_date.isoformat()}/proposal.json"
