from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from common.models import RunMetadata

# Where the proposal's actual_weights came from. "state" means they were read
# from the reconciled STATE/CURRENT item; "equal_weight_fallback" means no
# reconciled state existed and the drift is measured against a synthetic 1/N
# baseline that must never be approved as if it were the real portfolio.
BaselineSource = Literal["state", "equal_weight_fallback"]


class PortfolioMetrics(BaseModel):
    """Performance and concentration metrics for one allocation."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    # None = undefined for the period (no downside observations / drawdown
    # at the numerical floor); a descriptive metric never aborts a proposal.
    sortino_ratio: float | None
    max_drawdown: float
    calmar_ratio: float | None
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
    baseline_source: BaselineSource
    drift_l1: float
    benchmark_weights: dict[str, dict[str, float]]
    metrics: dict[str, PortfolioMetrics]
    covariance_shrinkage: float
    window_days: int = Field(ge=2)
    prices_ref: str
    excluded: dict[str, str]
    run_date: date
