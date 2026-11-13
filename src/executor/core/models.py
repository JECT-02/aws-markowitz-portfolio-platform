from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from common.models import RunMetadata


class OrderSide(StrEnum):
    """Direction of a submitted order."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """Lifecycle status of a submitted order as reported by the broker."""

    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class ProposalStatus(StrEnum):
    """Status values stored on the proposal item."""

    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class BrokerPosition(BaseModel):
    """Holding of one symbol as reported by the broker."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str
    quantity: float
    price: float

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized

    @field_validator("quantity", "price")
    @classmethod
    def _validate_non_negative(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("position fields must be finite and non-negative")
        return value


class PlannedOrder(BaseModel):
    """One order in the deterministic execution plan."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str
    side: OrderSide
    quantity: float
    notional: float
    client_order_id: str

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized

    @field_validator("quantity", "notional")
    @classmethod
    def _validate_positive(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("order fields must be finite and positive")
        return value


class OrderPlan(BaseModel):
    """Complete order plan: sells first, then buys, plus discarded dust."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    sales: list[PlannedOrder]
    buys: list[PlannedOrder]
    skipped: dict[str, str]

    @model_validator(mode="after")
    def _validate_ids(self) -> OrderPlan:
        identifiers = [order.client_order_id for order in self.sales + self.buys]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("client order identifiers must be unique")
        return self


class FilledOrder(BaseModel):
    """Outcome of one submitted order."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    client_order_id: str
    symbol: str
    side: OrderSide
    requested_quantity: float
    filled_quantity: float
    status: OrderStatus

    @field_validator("requested_quantity", "filled_quantity")
    @classmethod
    def _validate_non_negative(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0.0:
            raise ValueError("fill quantities must be finite and non-negative")
        return value


class Reconciliation(BaseModel):
    """Weights derived from real broker positions after execution."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    weights: dict[str, float]
    account_value: float
    reconciled_at: datetime

    @field_validator("account_value")
    @classmethod
    def _validate_account_value(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("account value must be finite and positive")
        return value

    @model_validator(mode="after")
    def _validate_weights(self) -> Reconciliation:
        if not self.weights:
            raise ValueError("reconciled weights must not be empty")
        total = math.fsum(self.weights.values())
        if any(not math.isfinite(weight) or weight < 0.0 for weight in self.weights.values()):
            raise ValueError("reconciled weights must be finite and non-negative")
        if abs(total - 1.0) > 1e-6:
            raise ValueError("reconciled weights must sum to one")
        return self


class ExecutionResult(BaseModel):
    """Final outcome of one rebalance execution."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    status: ProposalStatus
    orders: list[FilledOrder]
    reconciliation: Reconciliation | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> ExecutionResult:
        if self.status is ProposalStatus.FAILED and not self.error:
            raise ValueError("failed results must carry an error message")
        if self.status is not ProposalStatus.FAILED and self.error:
            raise ValueError("only failed results may carry an error message")
        return self


class ProposalRecord(BaseModel):
    """Proposal item as read from the state table."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    proposal_id: str
    status: str
    weights: dict[str, float]
    prices: dict[str, float]
    created_at: datetime
    expires_at: datetime
    metadata: RunMetadata

    @model_validator(mode="after")
    def _validate_weights(self) -> ProposalRecord:
        if not self.weights:
            raise ValueError("proposal weights must not be empty")
        if any(not math.isfinite(weight) for weight in self.weights.values()):
            raise ValueError("proposal weights must be finite")
        total = math.fsum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError("proposal weights must sum to one")
        for symbol, price in self.prices.items():
            if not math.isfinite(price) or price <= 0.0:
                raise ValueError(f"proposal price for {symbol} must be finite and positive")
        return self


class IdempotencyClaim(BaseModel):
    """Recorded idempotency claim for one execution attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str
    proposal_id: str
    status: str
    result: ExecutionResult | None = None
    expires_at: datetime | None = None


def decimal_string(value: float) -> str:
    """Serialize a finite float for DynamoDB numeric storage."""
    if not math.isfinite(value):
        raise ValueError("value must be finite")
    return str(Decimal(str(value)))
