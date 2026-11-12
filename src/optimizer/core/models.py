from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from common.models import RunMetadata


class PortfolioMetrics(BaseModel):
    """Performance and concentration metrics for one allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float
    herfindahl_index: float


class Proposal(BaseModel):
    """Optimizer output stored in the gold layer."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    metadata: RunMetadata
    created_at: datetime
    status: str
    rebalance_required: bool
    weights: dict[str, float]
    actual_weights: dict[str, float]
    drift_l1: float
    benchmark_weights: dict[str, dict[str, float]]
    metrics: dict[str, PortfolioMetrics]
    covariance_shrinkage: float
    window_days: int = Field(ge=2)
    excluded: dict[str, str]
    run_date: date
