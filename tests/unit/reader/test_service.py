from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from common.errors import DataValidationError, NotFoundError
from common.models import DataSource, RunMetadata
from optimizer.core.models import PortfolioMetrics, Proposal
from reader.core.models import CurrentState, MonitorSnapshot
from reader.core.service import ReaderService


def _metadata() -> RunMetadata:
    return RunMetadata(
        run_id="run-1",
        run_date=date(2025, 2, 3),
        code_version="abc123",
        source=DataSource.ALPACA,
        fetched_at=datetime(2025, 2, 3, 21, 30, tzinfo=UTC),
    )


def _proposal_document() -> dict[str, Any]:
    metadata = _metadata()
    return Proposal(
        proposal_id="2025-02-03-run-1",
        metadata=metadata,
        created_at=datetime(2025, 2, 3, 21, 45, tzinfo=UTC),
        status="PENDING",
        rebalance_required=True,
        weights={"SPY": 0.6, "QQQ": 0.4},
        actual_weights={"SPY": 0.5, "QQQ": 0.5},
        drift_l1=0.2,
        benchmark_weights={"equal_weight": {"SPY": 0.5, "QQQ": 0.5}},
        metrics={
            "optimizer": PortfolioMetrics(
                annualized_return=0.05,
                annualized_volatility=0.10,
                sharpe_ratio=0.3,
                sortino_ratio=0.4,
                max_drawdown=-0.2,
                calmar_ratio=0.25,
                herfindahl_index=0.52,
            )
        },
        covariance_shrinkage=0.1,
        window_days=252,
        prices_ref="2024-02-01..2025-01-31",
        excluded={},
        run_date=metadata.run_date,
    ).model_dump(mode="json")


class _Gold:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents
        self.requests: list[date | None] = []

    def load_proposal(self, run_date: date | None) -> dict[str, Any]:
        self.requests.append(run_date)
        key = (
            "latest.json" if run_date is None else f"run_date={run_date.isoformat()}/proposal.json"
        )
        try:
            return self.documents[key]
        except KeyError:
            raise NotFoundError(f"proposal not found for {key}") from None


class _State:
    def __init__(self, current: CurrentState | None) -> None:
        self.current = current
        self.reads = 0

    def read_current_state(self) -> CurrentState | None:
        self.reads += 1
        return self.current


def _service(
    documents: dict[str, dict[str, Any]] | None = None,
    current: CurrentState | None = None,
) -> tuple[ReaderService, _Gold, _State]:
    gold = _Gold(documents if documents is not None else {"latest.json": _proposal_document()})
    state = _State(current)
    return ReaderService(gold, state), gold, state


def test_latest_proposal_is_validated() -> None:
    service, _, _ = _service()
    proposal = service.get_proposal()
    assert proposal.proposal_id == "2025-02-03-run-1"
    assert proposal.status == "PENDING"
    assert proposal.prices_ref == "2024-02-01..2025-01-31"


def test_proposal_by_run_date_uses_the_partition_key() -> None:
    service, gold, _ = _service({"run_date=2025-02-03/proposal.json": _proposal_document()})
    proposal = service.get_proposal(date(2025, 2, 3))
    assert proposal.run_date == date(2025, 2, 3)
    assert gold.requests == [date(2025, 2, 3)]


def test_missing_proposal_raises_not_found() -> None:
    service, _, _ = _service()
    with pytest.raises(NotFoundError):
        service.get_proposal(date(2025, 2, 4))


def test_malformed_document_raises_data_validation() -> None:
    service, _, _ = _service({"latest.json": {"proposal_id": "x"}})
    with pytest.raises(DataValidationError):
        service.get_proposal()


def test_metrics_come_from_the_proposal() -> None:
    service, _, _ = _service()
    metrics = service.get_metrics()
    assert set(metrics) == {"optimizer"}
    assert metrics["optimizer"].herfindahl_index == 0.52


def test_monitor_prefers_reconciled_state() -> None:
    current = CurrentState(
        weights={"SPY": 0.7, "QQQ": 0.3},
        reconciled_at=datetime(2025, 2, 3, 22, 0, tzinfo=UTC),
        account_value=100_000.0,
    )
    service, _, state = _service(current=current)
    snapshot = service.get_monitor()
    assert isinstance(snapshot, MonitorSnapshot)
    assert snapshot.weights == {"SPY": 0.7, "QQQ": 0.3}
    assert snapshot.reconciled_at == datetime(2025, 2, 3, 22, 0, tzinfo=UTC)
    assert snapshot.drift_l1 == 0.2
    assert snapshot.proposal_id == "2025-02-03-run-1"
    assert state.reads == 1


def test_monitor_falls_back_to_proposal_weights_without_state() -> None:
    service, _, state = _service()
    snapshot = service.get_monitor()
    assert snapshot.weights == {"SPY": 0.5, "QQQ": 0.5}
    assert snapshot.reconciled_at is None
    assert state.reads == 1


def test_monitor_without_state_reader_uses_proposal_weights() -> None:
    gold = _Gold({"latest.json": _proposal_document()})
    service = ReaderService(gold, None)
    snapshot = service.get_monitor()
    assert snapshot.weights == {"SPY": 0.5, "QQQ": 0.5}
    assert snapshot.reconciled_at is None


def test_backtest_is_not_available_yet() -> None:
    service, _, _ = _service()
    with pytest.raises(NotFoundError):
        service.get_backtest()
