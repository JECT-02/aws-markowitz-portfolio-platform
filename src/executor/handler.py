from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.clock import SystemClock
from common.config import ExecutorConfig, RuntimeSettings, StorageSettings
from common.env import load_env_file
from common.errors import (
    BrokerError,
    ConfigurationError,
    DataValidationError,
    MarketClosed,
    NotFoundError,
    PortfolioError,
    PriceDriftExceeded,
    ProposalExpired,
    ProposalNotPending,
    StorageError,
)
from common.logging import configure_logging, get_logger
from common.storage import S3Storage
from executor.core.execution import RebalanceService
from executor.core.prices import GoldPricesRepository
from executor.core.state import DynamoExecutionState

logger = get_logger(__name__)

_CONTENT_TYPE = "application/json"
_REBALANCE_ROUTE = "POST /rebalance"


@dataclass(frozen=True)
class ExecutorDependencies:
    service: RebalanceService


def handler(event: Mapping[str, Any], _context: object | None = None) -> dict[str, Any]:
    """AWS Lambda entry point behind the authenticated rebalance endpoint."""
    load_env_file(Path(".env"))
    configure_logging()
    deps = build_dependencies()
    return run_executor(event, deps)


def run_executor(event: Mapping[str, Any], deps: ExecutorDependencies) -> dict[str, Any]:
    """Route one HTTP API v2 rebalance request to the execution service.

    Precondition: ``event`` follows the API Gateway HTTP API v2 payload.
    Postcondition: returns a Lambda proxy response; rejected executions map to
    4xx with safe messages and broker failures to 502.
    """
    route = event.get("routeKey")
    if route != _REBALANCE_ROUTE:
        return _response(404, {"message": f"no route for {route}"})
    try:
        proposal_id, idempotency_key = _parse_request(event)
        result = deps.service.execute(proposal_id, idempotency_key)
    except NotFoundError as exc:
        return _response(404, {"message": str(exc)})
    except DataValidationError as exc:
        return _response(400, {"message": str(exc)})
    except (ProposalExpired, ProposalNotPending, MarketClosed, PriceDriftExceeded) as exc:
        return _response(409, {"message": str(exc)})
    except BrokerError as exc:
        logger.exception("broker rejected the execution", extra={"route": route})
        return _response(502, {"message": str(exc)})
    except (StorageError, ConfigurationError):
        logger.exception("executor request failed", extra={"route": route})
        return _response(500, {"message": "internal error"})
    except PortfolioError as exc:
        logger.exception("executor request failed", extra={"route": route})
        return _response(500, {"message": str(exc)})
    except Exception:
        logger.exception("unexpected executor failure", extra={"route": route})
        return _response(500, {"message": "internal error"})
    logger.info(
        "rebalance executed",
        extra={
            "proposal_id": result.proposal_id,
            "status": result.status.value,
        },
    )
    return _response(200, result.model_dump(mode="json"))


def build_dependencies() -> ExecutorDependencies:
    """Build the execution dependencies from the environment."""
    runtime = RuntimeSettings.from_environment()
    config = ExecutorConfig.from_environment()
    storage_settings = StorageSettings.from_environment()
    table_name = os.environ.get("STATE_TABLE")
    if table_name is None or not table_name.strip():
        raise ConfigurationError("missing environment variable: STATE_TABLE")
    state = DynamoExecutionState(table_name.strip(), region_name=runtime.aws_region)
    storage = S3Storage(region_name=runtime.aws_region)
    prices = GoldPricesRepository(storage, storage_settings.gold_bucket)
    broker = _build_broker()
    service = RebalanceService(
        state=state,
        broker=broker,
        prices=prices,
        config=config,
        clock=SystemClock(),
    )
    return ExecutorDependencies(service=service)


def _build_broker() -> Any:
    """Return the production broker adapter.

    The Alpaca paper trading adapter lands with the broker integration branch;
    until then the endpoint cannot be deployed without one.
    """
    raise ConfigurationError("the production broker adapter is not wired yet (phase 5B)")


def _parse_request(event: Mapping[str, Any]) -> tuple[str, str]:
    headers = event.get("headers") or {}
    idempotency_key = None
    for name, value in headers.items():
        if name.lower() == "idempotency-key":
            idempotency_key = value
            break
    if not idempotency_key or not str(idempotency_key).strip():
        raise DataValidationError("the idempotency-key header is required")
    body = event.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else {}
    except json.JSONDecodeError as exc:
        raise DataValidationError("the request body must be valid json") from exc
    if not isinstance(payload, dict):
        raise DataValidationError("the request body must be a json object")
    proposal_id = payload.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise DataValidationError("proposal_id is required")
    return proposal_id.strip(), str(idempotency_key).strip()


def _response(status_code: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": _CONTENT_TYPE},
        "body": json.dumps(dict(payload), default=str),
    }
