from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from common.models import RunMetadata


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


class BacktestPeriodMetrics(BaseModel):
    """Risk and return summary for one backtest evaluation period."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float


class BacktestSegment(BaseModel):
    """Start/end dates of one backtest segment (mirrors backtest.core.models)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date
    end: date


class BacktestCandidate(BaseModel):
    """One hyperparameter combination evaluated across validation folds."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    gamma: float
    transaction_cost_lambda: float
    alpha: float
    window_days: int = Field(ge=2)
    validation_sharpe: float
    fold_metrics: list[BacktestPeriodMetrics]


class BacktestComparisonRow(BaseModel):
    """Net-of-costs comparison of one allocation against the benchmarks."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    mode: str
    in_sample_sharpe: float | None
    out_of_sample_sharpe: float | None
    out_of_sample_annualized_return: float | None
    out_of_sample_max_drawdown: float | None


class BacktestReport(BaseModel):
    """Read-only view of the weekly backtest artifact stored in gold.

    Mirrors ``backtest.core.models.BacktestReport``; the two schemas are
    pinned together by ``tests/fixtures/backtest_report.json``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    metadata: RunMetadata
    generated_at: datetime
    selected: BacktestCandidate
    comparison: list[BacktestComparisonRow]
    sensitivity: dict[str, BacktestPeriodMetrics]
    validation: dict[str, BacktestPeriodMetrics]
    periods: dict[str, BacktestSegment]
    in_sample: dict[str, BacktestPeriodMetrics]
    out_of_sample: dict[str, BacktestPeriodMetrics]
    candidates: list[BacktestCandidate]
