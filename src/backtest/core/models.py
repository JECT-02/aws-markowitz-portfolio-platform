from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backtest.core.validation import ParameterCandidate, PeriodMetrics, ValidationResult
from common.models import RunMetadata

MODES = ("optimizer", "equal_weight", "minimum_variance", "spy")


class BacktestPeriodMetrics(BaseModel):
    """Risk and return summary for one evaluation period."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    max_drawdown: float


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
    """Weekly backtest report stored in the gold layer."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    metadata: RunMetadata
    generated_at: datetime
    selected: BacktestCandidate
    comparison: list[BacktestComparisonRow]
    sensitivity: dict[str, BacktestPeriodMetrics]
    in_sample: dict[str, BacktestPeriodMetrics]
    out_of_sample: dict[str, BacktestPeriodMetrics]
    candidates: list[BacktestCandidate]

    @classmethod
    def from_validation_result(
        cls,
        result: ValidationResult,
        metadata: RunMetadata,
        generated_at: datetime,
    ) -> BacktestReport:
        """Build the report from a validation outcome.

        Preconditions: ``result`` comes from ``TemporalValidation.run`` on
        silver log returns; ``generated_at`` is timezone-aware.
        Postconditions: the report round-trips through JSON with finite
        floats and a deterministic key order.
        """
        return cls(
            metadata=metadata,
            generated_at=generated_at,
            selected=_candidate(result.selected),
            comparison=_comparison(result),
            sensitivity={
                str(window): BacktestPeriodMetrics(**asdict(metrics))
                for window, metrics in sorted(result.sensitivity.items())
            },
            in_sample=_period_metrics(result.in_sample_metrics),
            out_of_sample=_period_metrics(result.out_of_sample_metrics),
            candidates=[_candidate(candidate) for candidate in result.candidates],
        )


def _candidate(candidate: ParameterCandidate) -> BacktestCandidate:
    return BacktestCandidate(**asdict(candidate))


def _period_metrics(
    metrics: dict[str, PeriodMetrics],
) -> dict[str, BacktestPeriodMetrics]:
    return {mode: BacktestPeriodMetrics(**asdict(value)) for mode, value in metrics.items()}


def _comparison(result: ValidationResult) -> list[BacktestComparisonRow]:
    rows: list[BacktestComparisonRow] = []
    for mode in MODES:
        in_sample = result.in_sample_metrics.get(mode)
        out_of_sample = result.out_of_sample_metrics.get(mode)
        if in_sample is None and out_of_sample is None:
            continue
        rows.append(
            BacktestComparisonRow(
                mode=mode,
                in_sample_sharpe=_sharpe(in_sample),
                out_of_sample_sharpe=_sharpe(out_of_sample),
                out_of_sample_annualized_return=(
                    out_of_sample.annualized_return if out_of_sample is not None else None
                ),
                out_of_sample_max_drawdown=(
                    out_of_sample.max_drawdown if out_of_sample is not None else None
                ),
            )
        )
    return rows


def _sharpe(metrics: PeriodMetrics | None) -> float | None:
    if metrics is None:
        return None
    return metrics.sharpe_ratio
