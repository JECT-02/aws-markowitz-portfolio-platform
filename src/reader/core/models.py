from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CurrentState(BaseModel):
    """Reconciled broker state stored under STATE/CURRENT."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    weights: dict[str, float]
    reconciled_at: datetime | None = None
    account_value: float | None = None


class MonitorSnapshot(BaseModel):
    """Read-only view of the live portfolio for the fast loop endpoints."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    run_date: date
    drift_l1: float | None
    rebalance_required: bool
    weights: dict[str, float]
    reconciled_at: datetime | None = None
