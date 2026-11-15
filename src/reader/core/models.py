from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class CurrentState(BaseModel):
    """Reconciled broker state stored under STATE/CURRENT."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    weights: dict[str, float]
    reconciled_at: datetime | None = None
    account_value: float | None = None


class MonitorRecord(BaseModel):
    """One fast-loop observation stored under the MONITOR partition."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    weights_live: dict[str, float]
    drift_l1: float
    rebalance_required: bool
    proposal_id: str
    computed_at: str


class MonitorSnapshot(BaseModel):
    """Read-only view of the live portfolio for the fast loop endpoints."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    run_date: date
    drift_l1: float | None
    rebalance_required: bool
    weights: dict[str, float]
    reconciled_at: datetime | None = None
    # Live observation written by the 15-minute fast loop; absent until the
    # monitor has produced its first record.
    live_weights: dict[str, float] | None = None
    live_drift_l1: float | None = None
    monitor_updated_at: str | None = None
